"""Prompt construction for classification and extraction.

The load-bearing property here is that prompts are built entirely from
per-tenant data: no document-type literal may appear in code (design decision 2
-- this feature generalizes the hardcoded enum in
`knowledge/rag/pipeline/transformer/file_encoding.py`, it does not repeat it).
"""

from bisheng.doc_extract.domain.services.prompt_builder import (
    build_classify_prompt,
    build_extract_messages,
)

CANDIDATES = [
    {
        'doc_type': 'business_license',
        'name': '营业执照',
        'description': '工商行政管理部门颁发的企业登记证件，含企业名称、统一社会信用代码、法定代表人等',
    },
    {
        'doc_type': 'id_card',
        'name': '身份证',
        'description': '中华人民共和国居民身份证，含姓名、性别、民族、出生日期、住址、公民身份号码',
    },
]

SCHEMA = [
    {'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True},
    {'key': 'credit_code', 'label': '统一社会信用代码', 'type': 'string', 'required': True},
    {'key': 'valid_to', 'label': '营业期限至', 'type': 'date', 'required': False},
]


def test_classify_prompt_lists_every_candidate_type():
    prompt = build_classify_prompt(CANDIDATES)
    assert 'business_license' in prompt
    assert 'id_card' in prompt


def test_classify_prompt_carries_each_candidate_description():
    prompt = build_classify_prompt(CANDIDATES)
    for candidate in CANDIDATES:
        assert candidate['description'] in prompt


def test_classify_prompt_contains_no_hardcoded_doc_types():
    """A tenant with only one configured type must not see any other type name.

    This is the check that would fail if someone reintroduced a fixed enum.
    """
    prompt = build_classify_prompt([CANDIDATES[0]])
    assert 'business_license' in prompt
    assert 'id_card' not in prompt
    # Nothing from the old shougang taxonomy may leak in.
    for stale in ('政策制度', '标准规范', 'POL', 'STD'):
        assert stale not in prompt


def test_classify_prompt_asks_for_json_only():
    prompt = build_classify_prompt(CANDIDATES)
    assert 'JSON' in prompt
    assert 'doc_type' in prompt
    assert 'confidence' in prompt


def test_classify_prompt_with_no_candidates_still_builds():
    prompt = build_classify_prompt([])
    assert isinstance(prompt, str)
    assert prompt


def test_extract_messages_includes_every_field_key_and_label():
    messages = build_extract_messages('some document text', SCHEMA)
    joined = ' '.join(m['content'] for m in messages)
    for field in SCHEMA:
        assert field['key'] in joined
        assert field['label'] in joined


def test_extract_messages_marks_required_vs_optional():
    messages = build_extract_messages('text', SCHEMA)
    joined = ' '.join(m['content'] for m in messages)
    # The required flag must be visible to the model, in some form.
    assert joined.count('credit_code') >= 2 or 'required' in joined.lower() or '必填' in joined


def test_extract_messages_embeds_document_text():
    messages = build_extract_messages('营业执照正文内容', SCHEMA)
    joined = ' '.join(m['content'] for m in messages)
    assert '营业执照正文内容' in joined


def test_extract_messages_asks_for_json():
    messages = build_extract_messages('text', SCHEMA)
    joined = ' '.join(m['content'] for m in messages)
    assert 'JSON' in joined


def test_extract_messages_has_system_and_user_roles():
    messages = build_extract_messages('text', SCHEMA)
    roles = [m['role'] for m in messages]
    assert 'system' in roles
    assert 'user' in roles


def test_classify_prompt_is_deterministic():
    """Same input -> same prompt. Guards against dict/set ordering leaking in."""
    assert build_classify_prompt(CANDIDATES) == build_classify_prompt(CANDIDATES)
