"""F090: report data endpoint — happy path + owner/tenant access errors
(404 not found / 400 agents mismatch)."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bisheng.evaluation.api.endpoints import evaluation as evaluation_endpoints

BASE = "/api/v1/evaluation"


class _User:
    user_id = 7
    user_name = "u"


async def _login():
    return _User()


@pytest.fixture
def client():
    from bisheng.common.dependencies.user_deps import UserPayload

    app = FastAPI()
    app.include_router(evaluation_endpoints.router, prefix="/api/v1")
    app.dependency_overrides[UserPayload.get_login_user] = _login
    return TestClient(app, raise_server_exceptions=False)


def test_report_happy_path(client, monkeypatch):
    seen = {}

    def fake(group_id, user_id):
        seen["args"] = (group_id, user_id)
        return {"group_id": group_id, "evaluations": [{"version": 1}, {"version": 2}]}

    monkeypatch.setattr(evaluation_endpoints, "build_compare_report", fake)
    resp = client.get(f"{BASE}/compare/g1/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["group_id"] == "g1"
    assert seen["args"] == ("g1", 7)


def test_group_not_found_is_404(client, monkeypatch):
    def fake(group_id, user_id):
        raise HTTPException(status_code=404, detail="compare group not found")

    monkeypatch.setattr(evaluation_endpoints, "build_compare_report", fake)
    resp = client.get(f"{BASE}/compare/g1/report")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "compare group not found"


def test_agents_mismatch_is_400(client, monkeypatch):
    def fake(group_id, user_id):
        raise HTTPException(status_code=400, detail="compare group agents mismatch")

    monkeypatch.setattr(evaluation_endpoints, "build_compare_report", fake)
    resp = client.get(f"{BASE}/compare/g1/report")
    assert resp.status_code == 400
