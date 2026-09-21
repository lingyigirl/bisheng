"""A QA hit's answer must reach the model — it currently does not.

A QA knowledge base maps a question to an answer. `QA_save_knowledge` stores the
answer in the vector/ES metadata (`extra.answer`) and the QUESTION as the
document's `page_content`, because the question is what gets embedded and
matched. Retrieval works: a live query returns the right row with the answer in
its metadata.

What then happens is the defect this file pins: every retrieval path formats the
hit through `KnowledgeUtils.format_retrieved_chunk`, which renders only
`page_content` — the question. The answer never reaches the caller, so the model
is handed "银行的电话" and asked to answer a question about a phone number it was
never told. It can only invent one.

Three call sites share that formatter (workstation daily chat, the workflow RAG
node, the workflow knowledge-retrieval node), which is why the fix belongs in
the formatter rather than in any one of them.

`test_document_chunks_are_unchanged` is the guard on the other side: a document
chunk has no answer, and its output must stay byte-identical to before.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
from langchain_core.documents import Document

ANSWER = '12312312345'
QUESTION = '银行的电话'


@pytest.fixture(scope='module')
def KnowledgeUtils():
    """The REAL `KnowledgeUtils`, loaded around the session-wide import stub.

    `test/fixtures/mock_services.py` replaces the whole
    `bisheng.knowledge.domain.services.knowledge_utils` module in `sys.modules`
    with a stub (`premock_import_chain`, called unconditionally from conftest),
    so `from ... import KnowledgeUtils` yields a placeholder class with none of
    the real methods. That stub exists to break a circular import chain during
    discovery; it is not standing in for broken behaviour — the real module
    imports and works fine outside tests.

    Loading the file directly is what lets this test exercise the function that
    actually runs in production. Testing the stub would assert nothing.
    """
    path = (
        pathlib.Path(__import__('bisheng').__file__).parent
        / 'knowledge/domain/services/knowledge_utils.py'
    )
    spec = importlib.util.spec_from_file_location('_real_knowledge_utils', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.KnowledgeUtils


def _qa_doc() -> Document:
    """A QA hit exactly as retrieval returns one (shape verified against the DB).

    `extra` is a JSON STRING, not a dict — `QAKnowledgeMetadata.extra` is declared
    `Optional[str]`, and `QA_save_knowledge` serialises the answer into it.
    """
    return Document(
        page_content=QUESTION,
        metadata={
            'file_id': 1,
            'knowledge_id': '84',
            'chunk_index': 0,
            'citation_key': 'knowledgesearch_abc:0',
            'extra': json.dumps({'answer': ANSWER, 'main_question': QUESTION}, ensure_ascii=False),
        },
    )


def _document_doc() -> Document:
    """An ordinary document chunk: no `extra`, so nothing to inject."""
    return Document(
        page_content='不良贷款率指不良贷款占贷款总额的比例。',
        metadata={
            'file_id': 7,
            'knowledge_id': '12',
            'chunk_index': 3,
            'citation_key': 'knowledgesearch_def:3',
            'document_name': '报表.pdf',
        },
    )


# ── the defect ───────────────────────────────────────────────────────────────


def test_a_qa_hit_carries_its_answer_into_the_formatted_chunk(KnowledgeUtils):
    """THE bug. The formatted chunk is what the model is given; if the answer is
    absent from it, every downstream consumer — three retrieval paths, two
    conversation entries — is answering without the answer."""
    formatted = KnowledgeUtils.format_retrieved_chunk(_qa_doc())

    assert ANSWER in formatted, (
        'the QA answer did not reach the formatted chunk, so the model is given '
        f'the question without its answer. Got:\n{formatted}'
    )


def test_the_question_is_still_carried(KnowledgeUtils):
    """The answer is ADDED, not substituted: the question is what matched, and
    it is what the source label shows the user."""
    formatted = KnowledgeUtils.format_retrieved_chunk(_qa_doc())

    assert QUESTION in formatted


# ── the guard on the other side ──────────────────────────────────────────────


def test_document_chunks_are_unchanged(KnowledgeUtils):
    """A document chunk has no `extra.answer`. Its output must be exactly what
    the pre-fix formatter produced — this fix touches a function three retrieval
    paths share, so a document-library regression here would be silent."""
    formatted = KnowledgeUtils.format_retrieved_chunk(_document_doc())

    assert '不良贷款率指不良贷款占贷款总额的比例。' in formatted
    assert '<paragraph_content>' in formatted
    assert ANSWER not in formatted


def test_a_qa_hit_without_an_answer_is_not_annotated(KnowledgeUtils):
    """Malformed/unfinished rows exist (`status=FAILED` skips indexing, but a row
    can still be retrieved from an earlier successful index). An empty answer
    must not render an empty answer field."""
    doc = _qa_doc()
    doc.metadata['extra'] = json.dumps({'main_question': QUESTION}, ensure_ascii=False)

    formatted = KnowledgeUtils.format_retrieved_chunk(doc)

    assert QUESTION in formatted
    assert 'answer' not in formatted.lower() or ANSWER in formatted


def test_an_unparseable_extra_does_not_break_formatting(KnowledgeUtils):
    """`extra` is a string of unknown provenance. A malformed one must degrade to
    "no answer" rather than raise — this runs inside retrieval, where an
    exception costs the whole conversation its context."""
    doc = _qa_doc()
    doc.metadata['extra'] = 'not json at all'

    formatted = KnowledgeUtils.format_retrieved_chunk(doc)

    assert QUESTION in formatted
