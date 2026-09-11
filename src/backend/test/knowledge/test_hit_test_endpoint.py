"""F055 T-09: hit-test endpoint contract (test-first; T-10 implements).

AC-14: the KB detail page can test a question against the SAME retrieval logic
the workflow document-KB path uses, and see which files the effective-period
filter excluded. Tests pin:

- route ``POST /api/v1/knowledge/{knowledge_id}/hit_test`` with ``{question}``
- response shape: hits (document_name / chunk / score) + excluded counts
- the endpoint calls ``knowledge_flow_retrieval.retrieve_knowledge_documents``
  (design decision 7 — the shared builder, not a copy of the workflow logic)
- read permission + 404 semantics via ``KnowledgeService._get_readable_knowledge``
- excluded stats come from ``KnowledgeFileDao.count_non_effective_files_by_knowledge_id``

Until T-10 lands, the endpoint/builder symbols do not exist yet and these tests
are red — that is the intended test-first state.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from langchain_core.documents import Document
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode import BaseErrorCode
from bisheng.common.errcode.http_error import NotFoundError, UnAuthorizedError


# Error handlers mirroring bisheng.main (JSONResponse body carries the status;
# HTTP status stays 200 per the platform convention — see main.handle_http_exception).
def _handle_http_exception(req: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, BaseErrorCode):
        data = {"exception": str(exc), **exc.kwargs} if exc.kwargs else {"exception": str(exc)}
        return JSONResponse(content={"status_code": exc.code, "status_message": exc.message, "data": data})
    return JSONResponse(content={"status_code": 500, "status_message": str(exc)})


def _handle_validation_error(req: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(content={"status_code": 422, "status_message": exc.errors()})


def _mount_app(fake_service, login_user) -> FastAPI:
    """Minimal app mounting only the knowledge router, with the hit-test
    dependencies overridden (same pattern as test_knowledge_version_endpoint)."""
    from bisheng.knowledge.api.dependencies import get_knowledge_service
    from bisheng.knowledge.api.endpoints import knowledge as ep

    app = FastAPI()
    app.add_exception_handler(BaseErrorCode, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    api = APIRouter(prefix="/api/v1")
    api.include_router(ep.router)
    app.include_router(api)
    app.dependency_overrides[UserPayload.get_login_user] = lambda: login_user
    app.dependency_overrides[get_knowledge_service] = lambda: fake_service
    return app


def _login_user() -> SimpleNamespace:
    return SimpleNamespace(user_id=7, user_name="tester")


def _hit_test(client, knowledge_id: int = 5, question: str = "什么是授信政策?"):
    return client.post(f"/api/v1/knowledge/{knowledge_id}/hit_test", json={"question": question})


def _fake_hit_docs() -> list[Document]:
    return [
        Document(
            page_content="授信政策第三条: 不得突破额度上限。",
            metadata={
                "document_id": 11,
                "document_name": "授信政策.pdf",
                "chunk_index": 2,
                "rrf_score": 0.032,
                "knowledge_id": 5,
            },
        )
    ]


def _mock_effective_stats(expired: int = 1, not_yet_effective: int = 2):
    """F055: stub the hit-test excluded-stats DAO (no DB in unit tests)."""
    return patch(
        "bisheng.knowledge.domain.models.knowledge_file.KnowledgeFileDao.count_non_effective_files_by_knowledge_id",
        return_value=(expired, not_yet_effective),
        create=True,  # attribute lands in T-10; patch pre-creates it meanwhile
    )


def _mock_shared_builder(return_value):
    """Patch the endpoint's import of the shared builder (lands in T-10)."""
    return patch(
        "bisheng.knowledge.api.endpoints.knowledge.retrieve_knowledge_documents",
        return_value=return_value,
        create=True,  # attribute lands in T-10; patch pre-creates it meanwhile
    )


def test_hit_test_returns_hits_and_excluded_stats():
    """AC-14: 200 with hits (document_name / chunk / score) + excluded counts."""
    service = SimpleNamespace(_get_readable_knowledge=AsyncMock(return_value=SimpleNamespace(id=5, user_id=1)))
    app = _mount_app(service, _login_user())
    client = TestClient(app)

    with (
        _mock_shared_builder(_fake_hit_docs()),
        _mock_effective_stats(),
    ):
        r = _hit_test(client)

    assert r.status_code == 200
    body = r.json()
    assert body["status_code"] == 200
    hit = body["data"]["hits"][0]
    assert hit["document_id"] == 11
    assert hit["document_name"] == "授信政策.pdf"
    assert hit["chunk"] == "授信政策第三条: 不得突破额度上限。"
    assert hit["chunk_index"] == 2
    assert hit["score"] == 0.032
    assert hit["knowledge_id"] == 5
    assert body["data"]["excluded"] == {"expired": 1, "not_yet_effective": 2}


def test_hit_test_calls_shared_builder_with_kb_and_question():
    """The endpoint delegates to the shared builder with the KB id + question."""
    service = SimpleNamespace(_get_readable_knowledge=AsyncMock(return_value=SimpleNamespace(id=5, user_id=1)))
    app = _mount_app(service, _login_user())
    client = TestClient(app)

    with (
        _mock_shared_builder([]) as builder_mock,
        _mock_effective_stats(),
    ):
        r = _hit_test(client, question="监管处罚时限?")

    assert r.status_code == 200
    builder_mock.assert_called_once()
    kwargs = builder_mock.call_args.kwargs
    assert kwargs["knowledge_ids"] == [5]
    assert kwargs["query"] == "监管处罚时限?"


def test_hit_test_endpoint_builder_is_workflow_builder():
    """Design decision 7: endpoint and workflow share ONE builder (identity,
    not a copy) — the workflow module re-exports the knowledge-domain function."""
    import bisheng.workflow.common.knowledge as workflow_knowledge
    from bisheng.knowledge.domain.services import knowledge_flow_retrieval

    assert workflow_knowledge.retrieve_knowledge_documents is knowledge_flow_retrieval.retrieve_knowledge_documents


def test_hit_test_unknown_knowledge_returns_404_body():
    """Missing KB → body status_code 404 (platform error convention)."""
    service = SimpleNamespace(_get_readable_knowledge=AsyncMock(side_effect=NotFoundError(msg="knowledge not found")))
    app = _mount_app(service, _login_user())
    client = TestClient(app)

    with _mock_shared_builder([]):
        r = _hit_test(client, knowledge_id=999)

    assert r.json()["status_code"] == 404


def test_hit_test_no_read_permission_returns_403_body():
    """AC-17: hit-test honors the existing knowledge permission model (read)."""
    service = SimpleNamespace(_get_readable_knowledge=AsyncMock(side_effect=UnAuthorizedError(msg="no permission")))
    app = _mount_app(service, _login_user())
    client = TestClient(app)

    with _mock_shared_builder([]):
        r = _hit_test(client)

    assert r.json()["status_code"] == 403


def test_hit_test_missing_or_empty_question_422():
    """question is required and non-empty (request schema validation)."""
    service = SimpleNamespace(_get_readable_knowledge=AsyncMock(return_value=SimpleNamespace(id=5, user_id=1)))
    app = _mount_app(service, _login_user())
    client = TestClient(app)

    with _mock_shared_builder([]):
        r_missing = client.post("/api/v1/knowledge/5/hit_test", json={})
        r_empty = client.post("/api/v1/knowledge/5/hit_test", json={"question": ""})

    assert r_missing.json()["status_code"] == 422
    assert r_empty.json()["status_code"] == 422
