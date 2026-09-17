"""Scenario keywords and related questions for a finished answer (F059).

RUF001 is suppressed for this file: the prompts below are Chinese user-facing
copy and must use fullwidth punctuation, which is exactly what that rule calls
"ambiguous". Same reasoning as notification/external/_payload.py.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

from loguru import logger

from bisheng.knowledge.domain.services.knowledge_flow_retrieval import retrieve_knowledge_documents
from bisheng.llm.domain.services.llm import LLMService
from bisheng.sensitive_word.domain.services.platform_word_service import PlatformWordService

MAX_KEYWORDS = 6
MAX_QUESTIONS = 5
MAX_DOCS_FOR_QUESTIONS = 4
MAX_DOC_CHARS = 800
MODEL_TIMEOUT_SECONDS = 20

_CODE_BLOCK_RE = re.compile(r'```\w*\s*(.*?)```', re.DOTALL)


KEYWORD_PROMPT = """你是一个金融信贷领域的术语抽取助手。

从下面这轮对话的用户提问和助手回答中，抽取 {number} 个最关键的**业务术语或实体**
（例如：产品名、指标名、业务动作、监管概念、机构名）。

要求：
- 每个词不超过 12 个字，是名词或名词短语，不要整句
- 只抽取对话中**实际出现**的概念，不要联想或补充
- 不要抽取数字、标点、通用词（如"问题""情况""内容"）
- 不要输出任何涉及内容安全审查的词汇
- 只输出 JSON，不要解释

输出格式：{{"keywords": ["词1", "词2"]}}

用户提问：{question}
助手回答：{answer}
"""


QUESTION_PROMPT = """你是一个金融信贷领域的追问建议助手。

用户刚问了下面的问题，助手基于参考资料给出了回答。
请根据回答涉及的**知识点**，生成 {number} 个用户可能接着要问的问题。

要求：
- 每个问题是一个完整的疑问句，不超过 30 字
- 问题必须能从参考资料中找到答案依据
- 问题之间不要重复、不要是同义改写
- 只输出 JSON，不要解释

输出格式：{{"questions": ["问题1", "问题2"]}}

用户提问：{question}
助手回答：{answer}
参考资料：
{context}
"""


@dataclass
class SuggestionResult:
    """The two independent halves shown under an answer.

    Either may be empty, and an empty half means "do not render that section"
    (spec AC-32/AC-41). A failure in one half never empties the other.
    """

    keywords: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


class AnswerSuggestionService:
    """Scenario keywords + related questions for one finished answer (F059).

    Nothing here is persisted (spec AC-35/AC-45): the result is returned to the
    caller, shown once, and forgotten. That is also why no cache is needed — the
    frontend asks once per answer.

    The two halves are deliberately independent:

      keywords  — extracted from the round's own text; needs no knowledge base
                  (spec AC-34), so it works for every app
      questions — retrieved from the app's bound KB, then phrased; skipped
                  entirely when there is no binding (the retrieval is the
                  expensive part, so it is not merely discarded afterwards)

    Every failure path degrades to an empty half rather than raising: this is
    decorative content under an answer, and the user must never see an error for
    it (spec AC-32/AC-41).
    """

    @classmethod
    async def generate(
        cls,
        *,
        question: str,
        answer: str,
        invoke_user_id: int,
        tenant_id: int | None,
        flow_id: str | None,
        user_name: str = '',
    ) -> SuggestionResult:
        if not (question or '').strip() and not (answer or '').strip():
            # Nothing to derive from. Skipping also avoids spending a model call
            # on an empty prompt.
            return SuggestionResult()

        result = await cls._generate(
            question=question or '',
            answer=answer or '',
            invoke_user_id=invoke_user_id,
            tenant_id=tenant_id,
            flow_id=flow_id,
            user_name=user_name,
        )
        return await cls._strip_platform_words(result)

    @classmethod
    async def _generate(
        cls,
        *,
        question: str,
        answer: str,
        invoke_user_id: int,
        tenant_id: int | None,
        flow_id: str | None,
        user_name: str,
    ) -> SuggestionResult:
        llm = await cls._get_llm(invoke_user_id, tenant_id)
        if llm is None:
            # No `qa_similar_model` configured. NOT an error (spec AC-33): the
            # feature is simply unavailable. Note the existing
            # `knowledge_imp.recommend_question` raises here — this path must not,
            # see design 坑 4.
            return SuggestionResult()

        knowledge_ids = await cls.resolve_knowledge_ids(flow_id)

        keyword_task = cls._run_model(
            llm,
            KEYWORD_PROMPT.format(number=MAX_KEYWORDS, question=question, answer=answer),
            key='keywords',
        )
        question_task = cls._generate_questions(
            llm,
            question=question,
            answer=answer,
            knowledge_ids=knowledge_ids,
            invoke_user_id=invoke_user_id,
            user_name=user_name,
        )

        keywords, questions = await asyncio.gather(keyword_task, question_task, return_exceptions=True)
        return SuggestionResult(
            keywords=cls._as_list(keywords, 'keywords', limit=MAX_KEYWORDS),
            questions=cls._as_list(questions, 'questions', limit=MAX_QUESTIONS),
        )

    @classmethod
    async def _generate_questions(
        cls,
        llm: object,
        *,
        question: str,
        answer: str,
        knowledge_ids: list[int],
        invoke_user_id: int,
        user_name: str,
    ) -> list[str]:
        if not knowledge_ids:
            return []

        try:
            # `retrieve_knowledge_documents` is SYNCHRONOUS — it drives the vector
            # stores and reranker synchronously. It must run off the event loop
            # for the same reason the model call does: this is an async endpoint,
            # and a blocking retrieval stalls every other request in the process
            # while it runs (design 坑 5). Do not "simplify" this back to a bare
            # `await` — awaiting a non-coroutine raises TypeError.
            docs = await asyncio.to_thread(
                retrieve_knowledge_documents,
                knowledge_ids=knowledge_ids,
                query=question or answer,
                invoke_user_id=invoke_user_id,
                user_name=user_name,
                check_auth=True,
                max_content=MAX_DOC_CHARS,
                rrf_weights=[1.0, 1.0],
                top_k=MAX_DOCS_FOR_QUESTIONS,
            )
        except Exception:
            logger.opt(exception=True).warning('answer suggestions: retrieval failed')
            return []

        context = '\n---\n'.join(cls._doc_text(doc) for doc in (docs or []))[: MAX_DOC_CHARS * MAX_DOCS_FOR_QUESTIONS]
        if not context.strip():
            return []

        return await cls._run_model(
            llm,
            QUESTION_PROMPT.format(
                number=MAX_QUESTIONS, question=question, answer=answer, context=context
            ),
            key='questions',
        )

    @staticmethod
    def _doc_text(doc: object) -> str:
        text = getattr(doc, 'page_content', None)
        if text is None:
            text = str(doc)
        return text.strip()[:MAX_DOC_CHARS]

    @classmethod
    async def _run_model(cls, llm: object, prompt: str, *, key: str) -> list[str]:
        """Call the model off the event loop and parse the named JSON list out of it.

        The LLM slot hands back a **synchronous** chat model (`get_bisheng_llm_sync`),
        and invoking one from an async endpoint would block the event loop for the
        whole generation — under concurrency that stalls every other request in the
        process (design 坑 5). Hence the thread hop.

        Any failure (timeout, transport, unparseable output) yields an empty list;
        the caller decides what that means for its half.
        """
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(llm.invoke, prompt), timeout=MODEL_TIMEOUT_SECONDS
            )
        except Exception:
            logger.opt(exception=True).warning('answer suggestions: model call failed')
            return []

        content = getattr(response, 'content', response)
        if not isinstance(content, str):
            return []
        return cls._parse_list(content, key=key)

    @classmethod
    def _parse_list(cls, content: str, *, key: str) -> list[str]:
        """Extract the `key` list from model output that may be wrapped in prose or fences.

        `key` is required rather than inferred from the payload: a model that
        returns BOTH keys (it follows the shape it saw, not the one it was asked
        for) would otherwise hand the wrong list to whichever half parsed first —
        keywords rendered as "related questions", or vice versa.
        """
        payload = None
        for candidate in [*_CODE_BLOCK_RE.findall(content), content]:
            candidate = (candidate or '').strip()
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                payload = parsed
                break

        if not isinstance(payload, dict):
            return []

        value = payload.get(key)
        if isinstance(value, list):
            return cls._clean_list(value)
        return []

    @staticmethod
    def _clean_list(values: list) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned

    @classmethod
    def _as_list(cls, value: object, key: str, *, limit: int) -> list[str]:
        """Normalise one half of the gather result, absorbing its exception.

        `asyncio.gather(return_exceptions=True)` hands back the exception object
        itself; treating it as "empty" is what makes one half's failure invisible
        to the other (spec AC-32/AC-41).
        """
        if isinstance(value, BaseException) or not isinstance(value, list):
            if isinstance(value, BaseException):
                logger.opt(exception=value).warning('answer suggestions: {} half failed', key)
            return []
        return cls._clean_list(value)[:limit]

    @classmethod
    async def _strip_platform_words(cls, result: SuggestionResult) -> SuggestionResult:
        """Drop anything that hits the platform word library (spec AC-37 / INV-10).

        The platform library is mandatory and its contents are hidden from
        tenants. A suggestion echoing one of its words would turn a decorative
        element into a disclosure channel — the tenant would learn the exact term
        that blocks messages. Filtering both halves closes that off.

        Best-effort: if the library cannot be read, the suggestions are returned
        unfiltered rather than failing the request. The window is narrow (the
        library must be readable to enforce anything at all) and failing closed
        here would break the feature whenever the platform layer hiccups.
        """
        try:
            matcher = await PlatformWordService.build_platform_matcher()
        except Exception:
            logger.opt(exception=True).warning('answer suggestions: platform word filter skipped')
            return result
        if matcher is None:
            return result

        def _clean(values: list[str]) -> list[str]:
            kept: list[str] = []
            for value in values:
                scan = value if matcher.case_sensitive else value.lower()
                if matcher.automaton.find_all(scan):
                    continue
                kept.append(value)
            return kept

        return SuggestionResult(keywords=_clean(result.keywords), questions=_clean(result.questions))

    @staticmethod
    async def _get_llm(invoke_user_id: int, tenant_id: int | None):
        """Resolve the model slot, off the event loop.

        `get_knowledge_similar_llm` is SYNCHRONOUS and does real work — a typed
        config read plus the provider/model lookup that constructs the client
        (sync DB + credential decryption). Calling it directly from this async
        path blocks the loop for several round-trips per request, the same hazard
        the model call and retrieval are offloaded for (design 坑 5). Easy to
        miss because the call LOOKS like a plain getter.
        """
        try:
            return await asyncio.to_thread(
                LLMService.get_knowledge_similar_llm, invoke_user_id, tenant_id=tenant_id
            )
        except Exception:
            logger.opt(exception=True).warning('answer suggestions: model lookup failed')
            return None

    # ------------------------------------------------------------------
    # Knowledge base binding
    # ------------------------------------------------------------------

    @classmethod
    async def resolve_knowledge_ids(cls, flow_id: str | None) -> list[int]:
        """Knowledge bases bound to the app this conversation belongs to.

        Assistant-type apps carry their bindings in `assistant_link`; workflow and
        knowledge-space apps resolve their KBs inside the flow graph, which this
        service does not reach into. Returning nothing for those is deliberate —
        an unbound app simply shows no related questions (spec AC-42), which is
        better than guessing at a KB the user never scoped the app to.
        """
        if not flow_id:
            return []
        try:
            from bisheng.database.models.assistant import AssistantLinkDao

            links = await AssistantLinkDao.get_assistant_link(str(flow_id))
        except Exception:
            logger.opt(exception=True).warning('answer suggestions: KB binding lookup failed')
            return []

        seen: set[int] = set()
        ids: list[int] = []
        for link in links or []:
            knowledge_id = getattr(link, 'knowledge_id', None)
            if not knowledge_id or knowledge_id in seen:
                continue
            seen.add(knowledge_id)
            ids.append(int(knowledge_id))
        return ids
