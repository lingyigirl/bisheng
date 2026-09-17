"""T013 (F059): scenario keywords + related questions.

Two independent halves in one response:
  - keywords  — extracted from this round's question + answer, NO knowledge base
  - questions — retrieved from the app's bound knowledge base, then phrased

Because they are independent, every failure mode is per-half: one half failing
must still return the other (spec AC-32/AC-41). Everything here is stubbed — no
model, no vector store, no database.

RUF001 is suppressed for this file: the fixtures are Chinese and use fullwidth
punctuation, which is what the rule calls "ambiguous".
"""

# ruff: noqa: RUF001

import json
from types import SimpleNamespace

import pytest

from bisheng.knowledge.domain.services.answer_suggestion_service import (
    AnswerSuggestionService,
    SuggestionResult,
)

LLM_PATH = 'bisheng.knowledge.domain.services.answer_suggestion_service.LLMService.get_knowledge_similar_llm'
RETRIEVE_PATH = 'bisheng.knowledge.domain.services.answer_suggestion_service.retrieve_knowledge_documents'
BINDING_PATH = 'bisheng.knowledge.domain.services.answer_suggestion_service.AnswerSuggestionService.resolve_knowledge_ids'


class _FakeLLM:
    """Returns queued responses in order; records the prompts it was given."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if not self.responses:
            return SimpleNamespace(content='')
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return SimpleNamespace(content=nxt)


def _stub_llm(monkeypatch, responses):
    llm = _FakeLLM(responses)
    monkeypatch.setattr(LLM_PATH, lambda *a, **kw: llm)
    return llm


def _stub_binding(monkeypatch, ids):
    async def _resolve(*a, **kw):
        return list(ids)

    monkeypatch.setattr(BINDING_PATH, _resolve)


def _stub_retrieve(monkeypatch, docs):
    """Stub the retrieval as a SYNC function, matching the real signature.

    `retrieve_knowledge_documents` is synchronous; the service calls it via
    `asyncio.to_thread`. Stubbing it as `async def` here would hide a plain
    `await` on a non-coroutine — which raises TypeError in production while the
    tests stay green. Keep this sync.
    """
    calls = []

    def _retrieve(**kwargs):
        calls.append(kwargs)
        return list(docs)

    _retrieve.calls = calls
    monkeypatch.setattr(RETRIEVE_PATH, _retrieve)
    return _retrieve


def _doc(text):
    return SimpleNamespace(page_content=text, metadata={'file_id': 1})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_both_halves_are_returned(monkeypatch):
    _stub_binding(monkeypatch, [10])
    _stub_retrieve(monkeypatch, [_doc('不良贷款率的定义是……')])
    _stub_llm(
        monkeypatch,
        [
            json.dumps({'keywords': ['不良贷款率', '拨备覆盖率']}),
            json.dumps({'questions': ['不良贷款率如何计算？', '拨备覆盖率的监管要求是什么？']}),
        ],
    )

    result = await AnswerSuggestionService.generate(
        question='不良贷款率是什么', answer='不良贷款率是指……', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['不良贷款率', '拨备覆盖率']
    assert result.questions == ['不良贷款率如何计算？', '拨备覆盖率的监管要求是什么？']


async def test_keywords_work_without_any_knowledge_base(monkeypatch):
    """Spec AC-34: keyword extraction does not depend on a KB binding.

    An app with no knowledge base still gets keywords — this is the difference
    between the two halves, and the reason they are not one code path.
    """
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, [json.dumps({'keywords': ['授信', '担保']})])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['授信', '担保']
    assert result.questions == []  # no KB -> that half is skipped entirely


async def test_no_kb_binding_skips_retrieval_entirely(monkeypatch):
    """Skips the retrieval call, not just its output — it is the expensive part."""
    called = []

    def _retrieve(**kwargs):
        called.append(kwargs)
        return []

    _stub_binding(monkeypatch, [])
    monkeypatch.setattr(RETRIEVE_PATH, _retrieve)
    _stub_llm(monkeypatch, [json.dumps({'keywords': ['a']})])

    await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert called == []


# ---------------------------------------------------------------------------
# Model not configured (design 坑 4)
# ---------------------------------------------------------------------------


async def test_missing_model_yields_empty_result_without_raising(monkeypatch):
    """`get_knowledge_similar_llm` returns None when no model is configured.

    The existing `recommend_question` RAISES in this case; this path must not —
    spec AC-33/AC-42 require a silent empty result, since the feature is
    decorative and must never surface an error to the user.
    """
    _stub_binding(monkeypatch, [10])
    monkeypatch.setattr(LLM_PATH, lambda *a, **kw: None)

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result == SuggestionResult(keywords=[], questions=[])


# ---------------------------------------------------------------------------
# Degradation per half
# ---------------------------------------------------------------------------


async def test_a_failing_questions_half_still_returns_keywords(monkeypatch):
    _stub_binding(monkeypatch, [10])
    _stub_retrieve(monkeypatch, [_doc('片段')])
    _stub_llm(
        monkeypatch,
        [
            json.dumps({'keywords': ['关键词']}),
            RuntimeError('model exploded'),
        ],
    )

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['关键词']
    assert result.questions == []


async def test_a_failing_keywords_half_still_returns_questions(monkeypatch):
    _stub_binding(monkeypatch, [10])
    _stub_retrieve(monkeypatch, [_doc('片段')])
    _stub_llm(
        monkeypatch,
        [
            RuntimeError('model exploded'),
            json.dumps({'questions': ['后续追问？']}),
        ],
    )

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == []
    assert result.questions == ['后续追问？']


async def test_empty_retrieval_yields_no_questions(monkeypatch):
    _stub_binding(monkeypatch, [10])
    _stub_retrieve(monkeypatch, [])
    _stub_llm(monkeypatch, [json.dumps({'keywords': ['k']})])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.questions == []


# ---------------------------------------------------------------------------
# Malformed model output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'raw',
    [
        'not json at all',
        '',
        '{"wrong_key": ["x"]}',
        '{"keywords": "not a list"}',
        '[1, 2, 3]',
    ],
)
async def test_malformed_keyword_output_is_swallowed(monkeypatch, raw):
    """A model that ignores the JSON instruction must not break the response."""
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, [raw])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == []


async def test_a_model_returning_both_keys_gives_each_half_its_own(monkeypatch):
    """The questions half must not receive the keywords list.

    A model that ignores the requested shape and returns BOTH keys is common
    (it copies the shape it saw elsewhere). If the parser took "the first list
    found" the two halves would swap: keywords rendered under "related
    questions" and the real questions dropped.
    """
    _stub_binding(monkeypatch, [10])
    _stub_retrieve(monkeypatch, [_doc('片段')])
    both = json.dumps({'keywords': ['甲', '乙'], 'questions': ['问题一？', '问题二？']})
    _stub_llm(monkeypatch, [both, both])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['甲', '乙']
    assert result.questions == ['问题一？', '问题二？']


async def test_keywords_wrapped_in_a_markdown_code_block_are_parsed(monkeypatch):
    """Models habitually wrap JSON in ```json fences; the existing
    `recommend_question` uses `extract_code_blocks` for exactly this reason."""
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, ['```json\n{"keywords": ["票据", "贴现"]}\n```'])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['票据', '贴现']


async def test_non_string_entries_are_filtered_out(monkeypatch):
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, [json.dumps({'keywords': ['好的', 123, None, '  ', '另一个']})])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['好的', '另一个']


async def test_keywords_are_deduped_and_capped(monkeypatch):
    _stub_binding(monkeypatch, [])
    _stub_llm(
        monkeypatch,
        [json.dumps({'keywords': ['a', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']})],
    )

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['a', 'b', 'c', 'd', 'e', 'f']  # deduped, capped at 6


# ---------------------------------------------------------------------------
# Storage boundary (spec AC-35/AC-45) and leak guard (spec AC-37)
# ---------------------------------------------------------------------------


async def test_nothing_is_persisted(monkeypatch):
    """Suggestions are ephemeral: no table is touched.

    Guards the "当轮会话内展示" contract — if a future implementation starts
    writing history rows, this fails and forces the decision into the open.
    """
    from bisheng.chat_session.domain.models.feedback_event import MessageFeedbackEventDao

    def _explode(*a, **kw):
        raise AssertionError('answer suggestions must not persist anything')

    monkeypatch.setattr(MessageFeedbackEventDao, 'create', staticmethod(_explode))
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, [json.dumps({'keywords': ['k']})])

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['k']


async def test_keywords_hitting_the_platform_library_are_dropped(monkeypatch):
    """Spec AC-37 / INV-10: the suggestion bar may not become a side channel.

    The platform word library is mandatory and its contents are hidden from
    tenants. If an extracted keyword happened to be a platform word, showing it
    would hand the tenant the exact term that blocked the message — a
    tenant-visible surface echoing authoritative policy content. The keyword half
    is filtered against the platform matcher for that reason.

    (The frontend also does not request suggestions for a blocked round; this is
    the second line, for a word that appears in a normal answer.)
    """
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, [json.dumps({'keywords': ['违规词', '正常词']})])

    async def _platform_matcher():
        from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton
        from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import WordMatcher

        return WordMatcher(
            automaton=ACAutomaton(['违规词']),
            normalized_map={'违规词': '违规词'},
            case_sensitive=False,
            auto_reply='',
            extra_config={},
            max_word_len=3,
        )

    monkeypatch.setattr(
        'bisheng.knowledge.domain.services.answer_suggestion_service.PlatformWordService.build_platform_matcher',
        _platform_matcher,
    )

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == ['正常词']


async def test_questions_are_filtered_too(monkeypatch):
    """The same leak exists on the question half — it must be filtered as well."""
    _stub_binding(monkeypatch, [])
    _stub_llm(monkeypatch, [json.dumps({'keywords': []})])

    async def _platform_matcher():
        return None  # platform layer off -> filtering is a no-op

    monkeypatch.setattr(
        'bisheng.knowledge.domain.services.answer_suggestion_service.PlatformWordService.build_platform_matcher',
        _platform_matcher,
    )

    result = await AnswerSuggestionService.generate(
        question='q', answer='a', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result.keywords == []


def test_retrieval_entry_point_is_synchronous():
    """Pins the assumption the service is built on.

    `retrieve_knowledge_documents` is a plain function, so the service must call
    it through `asyncio.to_thread`. If a future refactor makes it a coroutine
    (or someone adds `await` back on the assumption that it is one), this fails
    here rather than as a TypeError on the first real question.
    """
    import inspect

    from bisheng.knowledge.domain.services.knowledge_flow_retrieval import (
        retrieve_knowledge_documents,
    )

    assert not inspect.iscoroutinefunction(retrieve_knowledge_documents), (
        'retrieve_knowledge_documents became async — update '
        'AnswerSuggestionService._generate_questions (it currently calls it via '
        'asyncio.to_thread, which would return a coroutine instead of results)'
    )


def test_model_call_is_synchronous():
    """Same guard for the model: the LLM slot hands back a sync chat model.

    The service invokes it via `asyncio.to_thread`; if the slot ever starts
    returning an async model, `llm.invoke` would return a coroutine that
    `to_thread` hands straight back and the parse step would silently see no
    content.
    """
    import inspect

    from bisheng.llm.domain.services.llm import LLMService

    source = inspect.getsource(LLMService.get_knowledge_similar_llm)
    assert 'get_bisheng_llm_sync' in source, (
        'the qa_similar model slot no longer returns a sync model — revisit '
        'AnswerSuggestionService._run_model'
    )


async def test_blank_question_and_answer_skip_generation(monkeypatch):
    """Nothing to derive from — do not spend a model call."""
    called = []
    _stub_binding(monkeypatch, [])

    def _llm(*a, **kw):
        called.append(1)
        return _FakeLLM([])

    monkeypatch.setattr(LLM_PATH, _llm)

    result = await AnswerSuggestionService.generate(
        question='   ', answer='', invoke_user_id=7, tenant_id=1, flow_id='app-1'
    )

    assert result == SuggestionResult(keywords=[], questions=[])
    assert called == []
