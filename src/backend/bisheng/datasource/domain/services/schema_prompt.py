"""Turn collected table metadata into what the model is shown, and no more.

Two modes, and which one ran is REPORTED rather than inferred (spec AC-28):

  * ``full`` — every exposed table's name, columns, types and the admin's
    business description. This is the good case: the model sees the whole
    schema and writes correct SQL without the platform guessing which tables
    the question is about.
  * ``retrieved`` — only the tables that look relevant to the question. Used
    when the full schema would blow the context budget.

The distinction is a first-class value (``INJECTION_FULL`` / ``INJECTION_RETRIEVED``,
re-exported from the query-log model so there is one set of names) because a
threshold nobody can observe is a threshold nobody can tune. It travels to the
execution record, the logs and the response.

**What is deliberately absent: sample values.** Spec AC-13 forbids reading
table rows, and a schema prompt is exactly where the temptation lives —
"showing the model three example values would help it infer the format".
It would also copy customer data into a prompt, a log and possibly a persisted
record, under an authorization model that does not cover any of those. An admin
who wants the model to understand a column writes it in `business_desc`.
"""

# ruff: noqa: RUF001 -- the injected schema block is Chinese prose shown to the
# model; fullwidth punctuation is correct in it, not a typo.

from __future__ import annotations

import re

from bisheng.datasource.domain.models.query_log import INJECTION_FULL, INJECTION_RETRIEVED

#: Rough character budget for the injected schema. Beyond this the prompt stops
#: being a description of the database and starts being a copy of it, and the
#: model's attention is spread across tables the question has nothing to do
#: with. The exact number is an implementation parameter -- the spec leaves it
#: to the implementation on purpose and only requires the two modes be
#: distinguishable.
#
# Sized from the content rather than picked round: a rendered table runs about
# 600 characters, so 60k holds roughly 100 tables. The first value here was
# 12k, which degraded at about 20 tables -- a perfectly ordinary schema -- and
# would have sent most users down the retrieval path, where answers are worse.
# The point of the threshold is to protect the context budget, not to trip.
SCHEMA_BUDGET_CHARS = 60000

#: Matches a chunk containing CJK, i.e. one that needs segmenting rather than
#: splitting. Covers the CJK Unified Ideographs block plus the extension-A range
#: that appears in personal names.
_CJK_RE = re.compile(r'[一-鿿㐀-䶿]')

#: Never retrieve fewer than this many tables, however weak the match. A single
#: keyword hit is a thin basis for deciding the other 40 tables are irrelevant.
MIN_RETRIEVED_TABLES = 3


def render_table(table: dict) -> str:
    """One table as the model sees it.

    Business description first: it is what tells the model the table is about
    customers at all, and the column list alone rarely does (`f_0031` says
    nothing).
    """
    lines = [f'### {table["table_name"]}']
    if table.get('business_desc'):
        lines.append(f'说明：{table["business_desc"]}')
    elif table.get('table_comment'):
        lines.append(f'说明：{table["table_comment"]}')

    for column in table.get('structure_snapshot') or []:
        name = column.get('name')
        col_type = column.get('type') or ''
        comment = column.get('comment')
        suffix = f'  -- {comment}' if comment else ''
        lines.append(f'  - {name} {col_type}{suffix}'.rstrip())
    return '\n'.join(lines)


def select_tables(tables: list[dict], question: str) -> tuple[list[dict], str]:
    """Choose what to inject and say which mode was used.

    ``tables`` are the EXPOSED ones (the caller filters `enabled`); an empty
    list is not special-cased here, the caller decides what an empty schema
    means.
    """
    rendered_all = [render_table(t) for t in tables]
    total = sum(len(r) for r in rendered_all)

    if total <= SCHEMA_BUDGET_CHARS:
        return tables, INJECTION_FULL

    return _retrieve(tables, question), INJECTION_RETRIEVED


def _retrieve(tables: list[dict], question: str) -> list[dict]:
    """Tables whose name or description shares a term with the question.

    Deliberately simple and explainable: matching on the admin's own words
    (table name, business description, column names) is something an operator
    can predict and fix by editing a description. Embedding-based retrieval
    would score better on paper and be untunable in practice -- when it picks
    the wrong table there is nothing the admin can change to make it pick the
    right one.
    """
    terms = _terms(question)
    if not terms:
        # No usable terms: fall back to the first N rather than injecting
        # nothing, which would look to the model like an empty database.
        return tables[:MIN_RETRIEVED_TABLES]

    scored: list[tuple[int, int, dict]] = []
    for order, table in enumerate(tables):
        haystack = _haystack(table)
        score = sum(1 for term in terms if term in haystack)
        scored.append((score, -order, table))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    hits = [table for score, _, table in scored if score > 0]
    if not hits:
        # Nothing matched. Injecting an empty schema would read to the model as
        # "this datasource has no tables", so a floor of the first few is the
        # honest degradation -- they are real tables, just not the best guesses.
        return tables[:MIN_RETRIEVED_TABLES]
    return hits


def _terms(question: str) -> list[str]:
    """Words from the question worth matching on.

    Chinese needs SEGMENTATION, not a split. `'订单有多少'` has no spaces, so a
    whitespace/punctuation split yields the whole sentence as one term and the
    word `订单` can never match a table description — measured, retrieval
    quietly did nothing for Chinese questions, which is the entire audience.

    `jieba` is already a dependency (the knowledge pipeline uses it) and is
    deterministic here: the same question always yields the same terms.

    Single characters are dropped: they match almost every description and turn
    the ranking into noise.
    """
    text = question or ''
    if not text.strip():
        return []

    words: list[str] = []
    # Split on punctuation first so the segmenter sees clause-sized input.
    for chunk in re.split(r'[\s,，。？?！!、;；:：()（）\[\]【】/\\|]+', text):
        if not chunk:
            continue
        if _CJK_RE.search(chunk):
            import jieba

            words.extend(jieba.lcut(chunk))
        else:
            words.append(chunk)

    seen: list[str] = []
    for word in (w.strip().lower() for w in words):
        if len(word) >= 2 and word not in seen:
            seen.append(word)
    return seen


def _haystack(table: dict) -> str:
    parts = [
        str(table.get('table_name') or ''),
        str(table.get('business_desc') or ''),
        str(table.get('table_comment') or ''),
    ]
    for column in table.get('structure_snapshot') or []:
        parts.append(str(column.get('name') or ''))
        parts.append(str(column.get('comment') or ''))
    return ' '.join(parts).lower()


def build_schema_block(tables: list[dict], question: str) -> tuple[str, str]:
    """The prompt block plus the mode that produced it.

    Returns the mode alongside the text so a caller cannot render one and
    forget to record the other -- the observability requirement is that the two
    always travel together (spec AC-28).
    """
    chosen, mode = select_tables(tables, question)
    if not chosen:
        return '', mode
    return '\n\n'.join(render_table(t) for t in chosen), mode
