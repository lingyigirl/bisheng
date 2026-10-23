"""Tests for the anonymous knowledge-graph neighbour lookup.

Covers the public endpoint's service contract:
- exact-name resolution to an entity id,
- empty graph (not error 10999) when the name is unknown,
- complete neighbourhood without the 100/300-row truncation,
- deterministic pick when one name exists under several entity types,
- body-parsed, login-free HTTP route.
"""

from contextlib import contextmanager

import pytest

from bisheng.knowledge.domain.models.knowledge_graph import (
    KnowledgeGraphEntity,
    KnowledgeGraphRelation,
)
from bisheng.knowledge.domain.schemas.knowledge_graph_schema import (
    KnowledgeGraphDataResp,
    KnowledgeGraphNode,
)
from bisheng.knowledge.domain.services import knowledge_graph_service as service_module
from bisheng.knowledge.domain.services.knowledge_graph_service import KnowledgeGraphService

KNOWLEDGE_ID = 148


@pytest.fixture()
def graph_session(db_session, monkeypatch):
    """Point the service at the transactional SQLite session used by the test."""

    @contextmanager
    def _session():
        yield db_session

    monkeypatch.setattr(service_module, "get_sync_db_session", _session)
    return db_session


def _entity(entity_id: int, name: str, entity_type: str = "概念", mention_count: int = 1):
    return KnowledgeGraphEntity(
        id=entity_id,
        tenant_id=1,
        knowledge_id=KNOWLEDGE_ID,
        name=name,
        normalized_name=name,
        entity_type=entity_type,
        mention_count=mention_count,
    )


def _relation(relation_id: int, source_id: int, target_id: int, rel_type: str = "关联"):
    return KnowledgeGraphRelation(
        id=relation_id,
        tenant_id=1,
        knowledge_id=KNOWLEDGE_ID,
        source_entity_id=source_id,
        target_entity_id=target_id,
        rel_type=rel_type,
        weight=0.8,
    )


def test_returns_complete_neighbourhood_without_truncation(graph_session):
    """A hub with 120 edges must come back whole (old API capped at 300, design at 100)."""
    center = _entity(1000, "中心实体")
    neighbours = [_entity(2000 + i, f"邻居{i}") for i in range(120)]
    relations = [_relation(3000 + i, center.id, 2000 + i) for i in range(120)]
    graph_session.add_all([center, *neighbours, *relations])
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, "中心实体")

    assert len(result.edges) == 120
    assert len(result.nodes) == 121
    assert result.truncated is False
    assert {node.id for node in result.nodes} == {center.id, *[item.id for item in neighbours]}


def test_unknown_entity_name_returns_empty_graph(graph_session):
    graph_session.add(_entity(1000, "中心实体"))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, "不存在的实体")

    assert result.nodes == []
    assert result.edges == []
    assert result.truncated is False


def test_entity_name_is_scoped_by_knowledge_id(graph_session):
    graph_session.add(_entity(1000, "中心实体"))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(9999, "中心实体")

    assert result.nodes == []


def test_isolated_entity_returns_only_itself(graph_session):
    graph_session.add(_entity(1000, "孤立实体"))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, "孤立实体")

    assert [node.id for node in result.nodes] == [1000]
    assert result.edges == []
    assert result.nodes[0].degree == 0


def test_normalized_name_matches_full_width_variant(graph_session):
    """Stored spelling differs but normalised form is identical — still an exact lookup."""
    graph_session.add(
        KnowledgeGraphEntity(
            id=1000,
            tenant_id=1,
            knowledge_id=KNOWLEDGE_ID,
            name="ＡＢＣ公司",
            normalized_name="abc公司",
            entity_type="组织",
            mention_count=1,
        )
    )
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, "abc公司")

    assert [node.name for node in result.nodes] == ["ＡＢＣ公司"]


def test_duplicate_name_across_types_picks_most_mentioned(graph_session):
    graph_session.add_all(
        [
            _entity(1000, "苹果", entity_type="产品", mention_count=3),
            _entity(1001, "苹果", entity_type="组织", mention_count=9),
            _entity(2000, "苹果公司"),
        ]
    )
    graph_session.add(_relation(3000, 1001, 2000, "旗下"))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, "苹果")

    assert {node.id for node in result.nodes} == {1001, 2000}
    assert [edge.label for edge in result.edges] == ["旗下"]


def test_blank_entity_name_returns_full_graph(graph_session):
    """A blank name means "whole KB", not "entity named blank"."""
    graph_session.add_all([_entity(1000, "中心实体"), _entity(2000, "邻居实体")])
    graph_session.add(_relation(3000, 1000, 2000))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, "   ")

    assert {node.id for node in result.nodes} == {1000, 2000}
    assert [edge.id for edge in result.edges] == [3000]
    assert result.truncated is False


def test_missing_entity_name_returns_full_graph(graph_session):
    graph_session.add(_entity(1000, "中心实体"))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID)

    assert [node.id for node in result.nodes] == [1000]
    assert result.truncated is False


def test_full_graph_is_capped_at_500_nodes(graph_session):
    """620 entities -> top 500 by mention_count, all surviving edges intact."""
    entities = [_entity(1000 + i, f"实体{i}", mention_count=i) for i in range(620)]
    kept = [entity for entity in entities if entity.mention_count >= 120]
    relations = [
        _relation(9000 + i, kept[i].id, kept[i + 1].id)
        for i in range(10)
    ]
    graph_session.add_all([*entities, *relations])
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID, None)

    assert len(result.nodes) == 500
    assert result.truncated is True
    assert min(node.mention_count for node in result.nodes) == 120
    assert len(result.edges) == 10


def test_full_graph_within_cap_is_not_truncated(graph_session):
    graph_session.add_all([_entity(1000, "甲"), _entity(2000, "乙")])
    graph_session.add(_relation(3000, 1000, 2000))
    graph_session.flush()

    result = KnowledgeGraphService.get_public_graph(KNOWLEDGE_ID)

    assert len(result.nodes) == 2
    assert len(result.edges) == 1
    assert result.truncated is False


def test_public_endpoint_accepts_body_without_login(monkeypatch):
    """Route is mounted without a UserPayload dependency and reads both args from the body."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bisheng.knowledge.api.endpoints import knowledge_graph as endpoint_module

    captured: dict[str, object] = {}

    def fake_lookup(knowledge_id: int, entity_name: str | None):
        captured["knowledge_id"] = knowledge_id
        captured["entity_name"] = entity_name
        return KnowledgeGraphDataResp(
            nodes=[
                KnowledgeGraphNode(id=470, name="张伟", type="人物", mention_count=8, degree=1),
            ],
            edges=[],
        )

    monkeypatch.setattr(KnowledgeGraphService, "get_public_graph", fake_lookup)

    app = FastAPI()
    app.include_router(endpoint_module.router)
    client = TestClient(app)

    response = client.post(
        "/knowledge/graph/public/neighbors",
        json={"knowledge_id": KNOWLEDGE_ID, "entity_name": "张伟"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_code"] == 200
    assert payload["data"]["nodes"][0]["name"] == "张伟"
    assert captured == {"knowledge_id": KNOWLEDGE_ID, "entity_name": "张伟"}


def test_public_endpoint_allows_omitting_entity_name(monkeypatch):
    """Body may carry knowledge_id only; the service then receives entity_name=None."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bisheng.knowledge.api.endpoints import knowledge_graph as endpoint_module

    captured: dict[str, object] = {}

    def fake_lookup(knowledge_id: int, entity_name: str | None):
        captured["entity_name"] = entity_name
        return KnowledgeGraphDataResp()

    monkeypatch.setattr(KnowledgeGraphService, "get_public_graph", fake_lookup)

    app = FastAPI()
    app.include_router(endpoint_module.router)
    client = TestClient(app)

    response = client.post(
        "/knowledge/graph/public/neighbors",
        json={"knowledge_id": KNOWLEDGE_ID},
    )

    assert response.status_code == 200
    assert captured == {"entity_name": None}
