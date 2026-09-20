"""Ask a question, get an answer backed by a real query — or a clean failure.

The loop is: build the schema block, ask the model for a statement, validate it
through `SqlGuard`, run it through the executor, and if the database rejects it,
feed the error BACK to the model and try again — at most three times
(spec AC-37/AC-38).

Three things about that loop are deliberate:

**The retry budget is small and hard.** Three rounds, then the feature reports
a failure. An unbounded "keep asking the model until it works" loop against a
customer's production database is an outage generator, and each round costs a
model call plus a query.

**What goes back to the model is the DATABASE's message, and what goes to the
USER is not.** The model needs the real error ("column f_0031 does not exist")
to fix its statement; the user must never see it, because a driver error can
carry the host, port, schema names and account (spec AC-39 / INV-19 clause 1).
Those two audiences are separated here rather than at the edge, so no caller
can accidentally forward the raw text.

**A failure does not take the turn down with it** (spec AC-40). If document
knowledge matched in the same round, the answer is still composed from that;
this feature reports "the database query failed" and steps aside.

No `SqlDatabaseChain`. Its own docstring warns it "may attempt commands like
DROP TABLE or INSERT if appropriately prompted", which is the opposite of what
AC-16/AC-17 require, and it is unused elsewhere in the repo (design §8-3).
"""

# ruff: noqa: RUF001 -- the user-facing messages and the generation prompt are
# Chinese prose; fullwidth punctuation is correct in them, not a typo.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from bisheng.common.models.config import ConfigKeyEnum
from bisheng.datasource.domain.models.dao import DbQueryFailureDao
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services import schema_prompt
from bisheng.datasource.domain.services.executor import (
    DatasourceExecutor,
    QueryOutcome,
    tables_from_statement,
)

#: Spec AC-38: never more than three rounds. After this the feature gives up and
#: says so -- each round is a model call plus a query against a live database.
MAX_RETRY_ROUNDS = 3

#: Failure categories kept as a small closed set so the admin list can group
#: them and a human can act on them (spec AC-42). The raw driver message is NOT
#: stored -- it can carry the host, account and schema names.
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('timeout', re.compile(r'timeout|timed out|lock wait', re.I)),
    ('permission', re.compile(r'denied|not authorized|no privilege|access', re.I)),
    ('unknown_table', re.compile(r"table .* doesn't exist|unknown table|no such table|invalid object", re.I)),
    ('unknown_column', re.compile(r"unknown column|no such column|invalid column|column .* not found", re.I)),
    # `syntax` alone, not `syntax error`: MySQL says "You have an error in your
    # SQL syntax", where the word the pattern needs sits at the END and the
    # literal phrase "syntax error" never appears. Matching the phrase alone
    # filed the commonest MySQL error under 'other'.
    ('syntax', re.compile(r'\bsyntax\b|parse error|near "', re.I)),
    ('connection', re.compile(r'connection|connect|network|refused|unreachable', re.I)),
]

_USER_MESSAGES = {
    'timeout': '查询超时，未能返回结果。可以尝试更具体的问题，或联系管理员检查数据库负载。',
    'permission': '当前数据源账号没有执行该查询的权限，请联系管理员。',
    'unknown_table': '查询涉及的表在当前数据源中不可用，请联系管理员确认已暴露的表。',
    'unknown_column': '查询涉及的字段不存在，可能是表的业务描述需要补充，请联系管理员。',
    'syntax': '生成的查询语句无法执行，请换一种说法再试。',
    'connection': '无法连接到数据源，请联系管理员检查连接配置。',
    'other': '查询未能完成，请联系管理员查看失败记录。',
}


@dataclass
class AnswerContext:
    """What the caller needs to render the answer and its citation."""

    answer: str
    outcome: QueryOutcome | None = None
    injection_mode: str = ''
    failure_category: str | None = None
    #: True when the round should still be answered from other sources
    #: (spec AC-40) -- the caller keeps whatever document knowledge it had.
    degraded: bool = False
    statements_tried: list[str] = field(default_factory=list)


def categorize_error(message: str) -> str:
    """Map a driver message to one of the closed categories."""
    for name, pattern in _CATEGORY_PATTERNS:
        if pattern.search(message or ''):
            return name
    return 'other'


def user_message_for(category: str | None) -> str:
    """The user-facing sentence for a failure. Never the driver's words."""
    return _USER_MESSAGES.get(category or 'other', _USER_MESSAGES['other'])


class DatasourceQueryService:
    """Orchestrates generation, validation, execution and retry. Stateless."""

    @classmethod
    async def answer_question(
        cls,
        datasource: DbDatasource,
        question: str,
        tables: list[dict],
        *,
        user_id: int | None = None,
    ) -> AnswerContext:
        """Answer `question` against `datasource`, retrying at most three times.

        `tables` are the datasource's EXPOSED tables (the caller filters
        `enabled`), so this method never sees a table the admin has not exposed.
        """
        schema_block, injection_mode = schema_prompt.build_schema_block(tables, question)

        tried: list[str] = []
        last_error = ''
        last_category: str | None = None

        for attempt in range(MAX_RETRY_ROUNDS):
            statement = await cls._generate(datasource, question, schema_block, tried, last_error)
            if not statement:
                last_category = 'other'
                last_error = 'the model produced no usable statement'
                continue

            tried.append(statement)
            try:
                outcome = await DatasourceExecutor.execute(
                    datasource,
                    statement,
                    user_id=user_id,
                    injection_mode=injection_mode,
                )
            except Exception as exc:
                # The message is kept for the NEXT round's prompt and never for
                # the user; see the module docstring.
                last_error = str(exc)
                last_category = categorize_error(last_error)
                logger.info(
                    'datasource query attempt {}/{} failed for datasource={} ({}): {}',
                    attempt + 1,
                    MAX_RETRY_ROUNDS,
                    datasource.id,
                    last_category,
                    last_error,
                )
                continue

            return AnswerContext(
                answer=cls._compose(outcome, datasource),
                outcome=outcome,
                injection_mode=injection_mode,
                statements_tried=tried,
            )

        await cls._record_failure(datasource, tried, last_category, user_id=user_id)
        return AnswerContext(
            answer=user_message_for(last_category),
            injection_mode=injection_mode,
            failure_category=last_category,
            degraded=True,
            statements_tried=tried,
        )

    # --- generation ----------------------------------------------------------

    @classmethod
    async def _generate(
        cls,
        datasource: DbDatasource,
        question: str,
        schema_block: str,
        tried: list[str],
        last_error: str,
    ) -> str:
        """Ask the model for one statement, optionally with a correction hint.

        Returns '' when the model produced nothing usable -- the caller treats
        that as a failed round rather than executing a guess.
        """
        model = await cls._resolve_model(datasource)
        if model is None:
            return ''

        prompt = cls._build_prompt(datasource, question, schema_block, tried, last_error)
        try:
            reply = await model.ainvoke(prompt)
        except Exception:
            logger.opt(exception=True).warning('datasource SQL generation failed')
            return ''

        return _extract_statement(_text_of(reply))

    @staticmethod
    async def _resolve_model(datasource: DbDatasource):
        """The SQL-generation model: its own slot, falling back to the chat model.

        Spec AC-27 requires the fallback and requires it to be SILENT -- an
        unconfigured slot is the normal state, not an error. Reuses F058's slot
        resolution rather than re-deriving it; the parsing rules there are not
        obvious (the column holds JSON text) and were learned the hard way.
        """
        from bisheng.llm.domain.services.llm import LLMService
        from bisheng.llm.domain.services.model_slot import resolve_slot_model_id

        model_id = resolve_slot_model_id(datasource.tenant_id, ConfigKeyEnum.DATASOURCE_SQL_LLM.value)
        if model_id is None:
            return None
        try:
            return LLMService.get_bisheng_llm_sync(model_id=model_id)
        except Exception:
            logger.opt(exception=True).warning(
                'datasource SQL slot model {} could not be built; treating as unconfigured', model_id
            )
            return None

    @staticmethod
    def _build_prompt(
        datasource: DbDatasource,
        question: str,
        schema_block: str,
        tried: list[str],
        last_error: str,
    ) -> str:
        parts = [
            '你是一个 SQL 生成助手。根据下面的数据库表结构，把用户的问题转写成一条查询语句。',
            '',
            f'数据库引擎：{datasource.engine}',
            '',
            '可用表结构：',
            schema_block or '（当前数据源没有已暴露的表）',
            '',
            '要求：',
            '- 只生成一条 SELECT 语句，不要生成任何写操作、结构变更或加锁读取',
            '- 只使用上面列出的表和字段，不要引用未暴露的对象',
            '- 只输出 SQL 本身，不要解释',
            '',
            f'用户问题：{question}',
        ]
        if tried:
            parts.extend(['', '你之前生成的语句执行失败了，请修正：'])
            for statement in tried:
                parts.append(f'  尝试过：{statement}')
            # The REAL error, because that is what lets the model fix the
            # statement. It is a prompt, not a response -- the user never sees it.
            parts.append(f'  数据库返回：{last_error}')
        return '\n'.join(parts)

    # --- output --------------------------------------------------------------

    @staticmethod
    def _compose(outcome: QueryOutcome, datasource: DbDatasource) -> str:
        """A short description of what came back; the rows travel separately.

        The answer text deliberately does NOT inline the rows -- the caller
        renders the result table from `outcome`, and duplicating them into the
        answer would put customer data into a second place (the message body)
        with its own retention.
        """
        if outcome.row_count == 0:
            return '查询已执行，但没有匹配的数据。'
        suffix = '（结果超过显示上限，已截断）' if outcome.truncated else ''
        return f'已从数据源 {datasource.name} 查询到 {outcome.row_count} 行数据。{suffix}'

    # --- failure trail -------------------------------------------------------

    @staticmethod
    async def _record_failure(
        datasource: DbDatasource,
        tried: list[str],
        category: str | None,
        *,
        user_id: int | None,
    ) -> None:
        """Store the statements and a summary. Never the rows or the question.

        INV-19 clause 3 / AC-65: the failing statements are kept (an admin needs
        them to see WHICH table was being queried), the error is reduced to a
        category, and the user's question is NOT stored -- it is conversation
        text, and this table is not a conversation store (INV-11).
        """
        if not tried:
            return
        try:
            await DbQueryFailureDao.acreate_best_effort(
                {
                    'tenant_id': datasource.tenant_id,
                    'user_id': user_id,
                    'datasource_id': datasource.id,
                    'statements': tried,
                    'error_category': category or 'other',
                    'tables': sorted({t for s in tried for t in tables_from_statement(s)}),
                }
            )
        except Exception:
            # The DAO is already best-effort, so this is belt-and-braces -- and
            # it earned its place: a test that stubbed the DAO with a raising
            # double showed the previous version propagating out of
            # `answer_question`, turning "the query failed" into an unhandled
            # exception right after the caller had already handled the failure.
            # Relying on a collaborator's internal try/except makes this
            # function's behaviour depend on something it does not control.
            logger.opt(exception=True).warning(
                'datasource query failure record write failed for datasource={}', datasource.id
            )


# --- parsing the model's reply ------------------------------------------------


def _text_of(reply: Any) -> str:
    if isinstance(reply, str):
        return reply
    content = getattr(reply, 'content', None)
    if isinstance(content, str):
        return content
    return str(reply or '')


def _extract_statement(text: str) -> str:
    """Pull one SQL statement out of a model reply.

    Models wrap SQL in fenced blocks, prefix it with "SQL:" and append an
    explanation, so the fences come off and a trailing semicolon goes with
    them. Nothing here is a safety step -- `SqlGuard` decides what may run; this
    only stops formatting from making a valid statement look invalid.
    """
    if not text:
        return ''

    fenced = re.search(r'```(?:sql)?\s*(.*?)```', text, re.DOTALL | re.IGNORECASE)

    # First line that looks like a statement, inside the fence when there is
    # one. Searching only for a match -- rather than falling back to the whole
    # reply -- is what keeps prose out: a model that answers "I cannot help
    # with that" has produced no statement, and passing that text on as one
    # would have the guard reject it and the retry loop burn a round on it.
    source = fenced.group(1) if fenced else text
    for line in source.splitlines():
        stripped = line.strip()
        if re.match(r'^(select|with)\b', stripped, re.IGNORECASE):
            return stripped.rstrip(';').strip()

    return ''
