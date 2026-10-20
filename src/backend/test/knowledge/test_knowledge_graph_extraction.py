"""Unit tests for knowledge-graph LLM output validation/normalisation.

No database, Redis or model is required: these are the pure rules that decide
what survives an extraction response.
"""

from bisheng.knowledge.domain.services.knowledge_graph_extraction import (
    MAX_ENTITIES_PER_CHUNK,
    MAX_RELATIONS_PER_CHUNK,
    _content_to_text,
    _json_loads,
    normalize_entity_name,
    validate_extraction_payload,
)

ENTITY_TYPES = ["人物", "组织", "地点", "事件", "产品", "概念"]


def test_normalize_entity_name_strips_whitespace_and_punctuation():
    assert normalize_entity_name(" 张 伟。") == "张伟"
    assert normalize_entity_name("ＡＢＣ　公司") == "abc公司"
    assert normalize_entity_name("   ") == ""


def test_validate_extraction_payload_accepts_valid_entities_and_relations():
    payload = {
        "entities": [
            {"name": "张伟", "type": "人物", "description": "技术总监", "attributes": {"职位": "技术总监"}},
            {"name": "某公司", "type": "组织", "description": "一家公司"},
        ],
        "relations": [
            {
                "source": "张伟",
                "target": "某公司",
                "type": "任职于",
                "description": "张伟在某公司任职",
                "weight": 0.9,
            }
        ],
    }

    entities, relations = validate_extraction_payload(payload, ENTITY_TYPES)

    assert len(entities) == 2
    assert entities[0]["normalized_name"] == "张伟"
    assert entities[0]["attributes"] == {"职位": "技术总监"}
    assert len(relations) == 1
    assert relations[0]["source_normalized"] == "张伟"
    assert relations[0]["target_normalized"] == "某公司"
    assert relations[0]["weight"] == 0.9


def test_validate_unknown_entity_type_falls_back_to_other():
    payload = {
        "entities": [{"name": "未知实体", "type": "不存在的类型"}],
        "relations": [],
    }

    entities, _ = validate_extraction_payload(payload, ENTITY_TYPES)

    assert len(entities) == 1
    assert entities[0]["entity_type"] == "其他"


def test_validate_drops_relations_with_missing_endpoint():
    payload = {
        "entities": [{"name": "张伟", "type": "人物"}],
        "relations": [
            {"source": "张伟", "target": "不存在的人", "type": "同事"},
        ],
    }

    _, relations = validate_extraction_payload(payload, ENTITY_TYPES)

    assert relations == []


def test_validate_merges_duplicate_entities_and_attributes():
    payload = {
        "entities": [
            {"name": "张伟", "type": "人物", "description": "短描述", "attributes": {"职位": "总监"}},
            {"name": "张 伟", "type": "人物", "description": "更长的描述", "attributes": {"部门": "技术部"}},
        ],
        "relations": [],
    }

    entities, _ = validate_extraction_payload(payload, ENTITY_TYPES)

    assert len(entities) == 1
    assert entities[0]["description"] == "更长的描述"
    assert entities[0]["attributes"] == {"职位": "总监", "部门": "技术部"}


def test_validate_caps_entities_and_relations():
    entities = [
        {"name": f"实体{i}", "type": "概念"}
        for i in range(MAX_ENTITIES_PER_CHUNK + 5)
    ]
    relations = [
        {
            "source": f"实体{i % MAX_ENTITIES_PER_CHUNK}",
            "target": f"实体{(i + 1) % MAX_ENTITIES_PER_CHUNK}",
            "type": f"关系{i}",
        }
        for i in range(MAX_RELATIONS_PER_CHUNK + 5)
    ]

    accepted_entities, accepted_relations = validate_extraction_payload(
        {"entities": entities, "relations": relations},
        ENTITY_TYPES,
    )

    assert len(accepted_entities) == MAX_ENTITIES_PER_CHUNK
    assert len(accepted_relations) == MAX_RELATIONS_PER_CHUNK


def test_validate_rejects_malformed_payload():
    assert validate_extraction_payload(None, ENTITY_TYPES) == ([], [])
    assert validate_extraction_payload("not a dict", ENTITY_TYPES) == ([], [])
    assert validate_extraction_payload({"entities": "x"}, ENTITY_TYPES) == ([], [])


def test_json_loads_tolerates_markdown_fence():
    payload = _json_loads("```json\n{\"entities\": [], \"relations\": []}\n```")
    assert payload == {"entities": [], "relations": []}


def test_content_to_text_flattens_block_list():
    assert _content_to_text("hello") == "hello"
    assert _content_to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"


def test_resolve_entity_types_uses_config_and_caps(monkeypatch):
    from bisheng.knowledge.domain.services import knowledge_graph_service as mod

    class _Cfg:
        graph_entity_types = None
        extract_title_model_id = 1

    monkeypatch.setattr(
        mod.LLMService,
        "get_knowledge_llm",
        classmethod(lambda cls: _Cfg()),
    )

    defaults = mod.KnowledgeGraphService._resolve_entity_types(None)
    assert "人物" in defaults
    assert defaults[-1] == "其他"

    capped = mod.KnowledgeGraphService._resolve_entity_types([f"类型{i}" for i in range(30)])
    assert len(capped) == mod.MAX_ENTITY_TYPES
    assert capped[-1] == "其他"


def test_resolve_model_id_falls_back_and_errors(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from bisheng.knowledge.domain.services import knowledge_graph_service as mod

    class _Cfg:
        graph_model_id = 12
        extract_title_model_id = 34

    monkeypatch.setattr(
        mod.LLMService,
        "get_knowledge_llm",
        classmethod(lambda cls: _Cfg()),
    )
    assert mod.KnowledgeGraphService._resolve_model_id(None) == 12
    assert mod.KnowledgeGraphService._resolve_model_id(99) == 99

    class _EmptyCfg:
        graph_model_id = None
        extract_title_model_id = None

    monkeypatch.setattr(
        mod.LLMService,
        "get_knowledge_llm",
        classmethod(lambda cls: _EmptyCfg()),
    )
    with pytest.raises(HTTPException):
        mod.KnowledgeGraphService._resolve_model_id(None)
