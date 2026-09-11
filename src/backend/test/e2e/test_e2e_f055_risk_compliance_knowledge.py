"""E2E tests for F055 风险合规知识库 (risk-compliance knowledge base).

Runs against the live backend (default http://localhost:7860) with the full
middleware stack: upload → real parse/embed (knowledge_celery + Milvus + ES) →
``POST /knowledge/{id}/hit_test`` — the SAME retrieval builder the workflow
document-KB path uses (design decision 7), so the hit-test e2e exercises the
production retrieval pipeline.

Admin login: ``E2E_ADMIN_PASSWORD`` env (local dev: Tansun@2020).

Data isolation: every resource is ``e2e-f055-`` prefixed; class-scoped fixture
cleans leftovers at setup and removes what this run created at teardown.

Covers (API level):
- AC-01: preset classification tags (行内制度/监管法规/行业分析/历史案例) available right after KB creation
- AC-02: a newly created tag appears immediately in the KB tag list
- AC-04: deleting a tag cascades — file rows no longer carry the tag
- AC-05/AC-06/AC-07/AC-08: effective-period filtering of real retrieval
  (null = unlimited; future-effective excluded; expired excluded; clearing the
  dates takes effect immediately, no re-parse)
- AC-09/AC-10: weight/priority write path roundtrip via KB update API
- AC-14: hit-test returns hits (document/chunk/score) + exclusion stats
- AC-16: a static file ingested via the normal upload pipeline participates
  in the same retrieval path
- AC-17: read/write permission pairs — user without KB access gets 403

Deferred to the manual checklist (heavy or non-API ACs):
- AC-03/AC-09/AC-10/AC-12/AC-13: workflow-node retrieval semantics (tags /
  weight-priority ordering / top_k-threshold) are covered by unit tests at the
  shared builder (T-06/T-08/T-10); the actual workflow chat run is manual.
- AC-15: MCP realtime-data diversion policy is a design constraint, not an API.
- AC-18: multi-tenancy is disabled in this deployment (config default); the
  tenant-filter structure is covered by unit tests.
"""

import asyncio
import datetime
import json

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200, assert_resp_error
from test.e2e.helpers.auth import auth_headers, get_admin_token, get_user_token

PREFIX = "e2e-f055-"
KB1_NAME = f"{PREFIX}risk-kb"
KB2_NAME = f"{PREFIX}aux-kb"
TAG_NAME = f"{PREFIX}自定义标签"

FILE_A_NAME = f"{PREFIX}doc-effective.txt"
FILE_B_NAME = f"{PREFIX}doc-future.txt"
FILE_A_CONTENT = (
    "流动资金贷款额度核定办法：单户流动资金贷款授信额度不得超过其上一年度销售收入的"  # noqa: RUF001
    "百分之三十，且须经风险审查委员会审批。"  # noqa: RUF001
)
FILE_B_CONTENT = (
    "票据贴现业务操作规程：办理银行承兑汇票贴现须核验贸易背景真实性，"  # noqa: RUF001
    "贴现利率不得低于人民银行同期再贴现利率。"
)
QA_A = "流动资金贷款额度如何核定？"  # noqa: RUF001
QA_B = "票据贴现业务如何操作？"  # noqa: RUF001

PRESET_TAGS = {"行内制度", "监管法规", "行业分析", "历史案例"}

ERR_DUP_TAG = 10985
ERR_TAG_MISSING = 10986
ERR_FORBIDDEN = 403
ERR_NOT_FOUND = 404

FILE_STATUS_SUCCESS = 2
READY_TIMEOUT_S = 240
POLL_INTERVAL_S = 5

# Class-scoped shared state. (pytest-asyncio does NOT bind fixtures to the test
# method's `self` — a fixture's `self` is a different instance — so class-wide
# state lives here instead.)
_STATE: dict = {}


def _iso_date(days_offset: int) -> str:
    """ISO datetime `days_offset` days from today at 00:00 (server local tz)."""
    day = datetime.date.today() + datetime.timedelta(days=days_offset)
    return f"{day.isoformat()}T00:00:00"


async def _iter_knowledge(client: httpx.AsyncClient, token: str):
    """Iterate all NORMAL knowledge bases (cursor pagination)."""
    cursor = None
    while True:
        params = {"type": 0, "page_size": 50}
        if cursor:
            params["cursor"] = cursor
        data = assert_resp_200(await client.get("/knowledge", params=params, headers=auth_headers(token)))
        for item in data.get("data", []):
            yield item
        if data.get("has_more") and data.get("next_cursor"):
            cursor = data["next_cursor"]
        else:
            return


async def _delete_kb(client: httpx.AsyncClient, token: str, kb_id: int):
    """DELETE /knowledge/ takes its payload in the body (httpx: `content=` not `json=`)."""
    headers = {**auth_headers(token), "Content-Type": "application/json"}
    resp = await client.request("DELETE", "/knowledge/", content=json.dumps({"knowledge_id": kb_id}), headers=headers)
    assert_resp_200(resp)


async def _discover_embedding_model(client: httpx.AsyncClient, token: str) -> str:
    """Reuse an embedding model id from an existing knowledge base, if any."""
    async for item in _iter_knowledge(client, token):
        if item.get("model"):
            return str(item["model"])
    return "3"


async def _file_rows(client: httpx.AsyncClient, token: str, kb_id: int) -> list[dict]:
    data = assert_resp_200(
        await client.get(
            f"/knowledge/file_list/{kb_id}",
            params={"page": 1, "limit": 50},
            headers=auth_headers(token),
        )
    )
    return data.get("data", [])


async def _wait_files_ready(client: httpx.AsyncClient, token: str, kb_id: int) -> bool:
    """Poll until all uploaded files reach SUCCESS (or timeout)."""
    deadline = asyncio.get_event_loop().time() + READY_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        rows = await _file_rows(client, token, kb_id)
        if rows and all(r.get("status") == FILE_STATUS_SUCCESS for r in rows):
            return True
        if any(r.get("status") in (3, 6) for r in rows):  # FAILED / TIMEOUT
            return False
        await asyncio.sleep(POLL_INTERVAL_S)
    return False


class TestE2EF055RiskCompliance:
    """E2E: F055 风险合规知识库 — tags / effective dates / weight / hit-test."""

    @pytest.fixture(scope="class", autouse=True)
    async def shared(self):
        """Class-scoped lifecycle: cleanup leftovers → create KBs + upload files
        → yield → delete what this run created."""
        async with httpx.AsyncClient(base_url=API_BASE, timeout=60.0) as client:
            admin_token = await get_admin_token(client)
            admin_headers = auth_headers(admin_token)

            # Clear leftovers from a previous run (prefix-guarded).
            async for item in _iter_knowledge(client, admin_token):
                if str(item.get("name", "")).startswith(PREFIX):
                    await _delete_kb(client, admin_token, item["id"])

            # AC-01: preset tags are initialized on creation (asserted in test).
            model = await _discover_embedding_model(client, admin_token)
            for kb_name in (KB1_NAME, KB2_NAME):
                created = assert_resp_200(
                    await client.post(
                        "/knowledge/create",
                        json={"name": kb_name, "type": 0, "description": "e2e f055", "model": model},
                        headers=admin_headers,
                    )
                )
                if kb_name == KB1_NAME:
                    _STATE["kb1_id"] = created["id"]
                else:
                    _STATE["kb2_id"] = created["id"]

            # AC-16: static files via the normal upload pipeline — two-step
            # upload (/upload/{id} → MinIO raw object) + process (/process →
            # file rows + parse/embed enqueue on knowledge_celery), same as the
            # platform UI. File rows exist immediately after /process.
            _STATE["file_ids"] = {}
            for fname, content in ((FILE_A_NAME, FILE_A_CONTENT), (FILE_B_NAME, FILE_B_CONTENT)):
                up = assert_resp_200(
                    await client.post(
                        f"/knowledge/upload/{_STATE['kb1_id']}",
                        files={"file": (fname, content.encode("utf-8"), "text/plain")},
                        headers=admin_headers,
                    )
                )
                proc = assert_resp_200(
                    await client.post(
                        "/knowledge/process",
                        json={"knowledge_id": _STATE["kb1_id"], "file_list": [{"file_path": up["file_path"]}]},
                        headers=admin_headers,
                    )
                )
                assert isinstance(proc, list) and proc, f"process returned no file rows: {proc}"
                _STATE["file_ids"][fname] = proc[0]["id"]

            # FILE_B is future-effective from the start (self-contained state —
            # retrieval tests must not depend on the effective-period test
            # having run first, e.g. under `pytest -k ac14`).
            assert_resp_200(
                await client.put(
                    "/knowledge/file/effective_period",
                    json={
                        "knowledge_id": _STATE["kb1_id"],
                        "file_id": _STATE["file_ids"][FILE_B_NAME],
                        "effective_date": _iso_date(2),
                        "expire_date": None,
                    },
                    headers=admin_headers,
                )
            )

            yield {
                "client": client,
                "admin_token": admin_token,
            }

            # Teardown: remove this run's KBs (files/tags go with them).
            for kb_id in (_STATE.get("kb1_id"), _STATE.get("kb2_id")):
                if kb_id:
                    try:
                        await _delete_kb(client, admin_token, kb_id)
                    except Exception:
                        pass  # best-effort cleanup

    # ───────────────────────── fixtures ─────────────────────────

    @pytest.fixture
    async def client(self, shared):
        return shared["client"]

    @pytest.fixture
    async def admin_token(self, shared):
        return shared["admin_token"]

    @pytest.fixture
    async def user_token(self, client):
        return await get_user_token(client, "test", "Tansun@2020")

    async def _resolve_files(self, client, admin_token) -> dict:
        """Map uploaded file names → row ids (captured at /process time)."""
        if _STATE.get("file_ids") is None:
            rows = await _file_rows(client, admin_token, _STATE["kb1_id"])
            _STATE["file_ids"] = {r.get("file_name") or r.get("name"): r["id"] for r in rows}
        assert FILE_A_NAME in _STATE["file_ids"] and FILE_B_NAME in _STATE["file_ids"], _STATE["file_ids"]
        return _STATE["file_ids"]

    # ───────────────────────── AC-01: preset tags ─────────────────────────

    async def test_ac01_preset_classification_tags(self, client, admin_token):
        """AC-01: creating a KB makes the 4 preset classification tags available."""
        data = assert_resp_200(
            await client.get(
                f"/knowledge/{_STATE['kb1_id']}/tags",
                params={"page": 1, "limit": 20},
                headers=auth_headers(admin_token),
            )
        )
        names = {t["name"] for t in data["data"]}
        assert PRESET_TAGS <= names, f"preset tags missing: {PRESET_TAGS - names}"
        assert data["total"] >= 4

    # ───────────────────────── AC-02: tag CRUD ─────────────────────────

    async def test_ac02_new_tag_visible_immediately(self, client, admin_token):
        """AC-02: a tag created via API appears in the KB tag list right away."""
        headers = auth_headers(admin_token)
        tag = assert_resp_200(
            await client.post(
                "/knowledge/tags",
                json={"knowledge_id": _STATE["kb1_id"], "tag_name": TAG_NAME},
                headers=headers,
            )
        )
        _STATE["custom_tag_id"] = tag["id"]

        data = assert_resp_200(
            await client.get(f"/knowledge/{_STATE['kb1_id']}/tags", params={"page": 1, "limit": 20}, headers=headers)
        )
        assert any(t["name"] == TAG_NAME for t in data["data"]), data["data"]

    async def test_ac02b_duplicate_tag_rejected(self, client, admin_token):
        """AC-02: creating a same-name tag again → 10985."""
        resp = await client.post(
            "/knowledge/tags",
            json={"knowledge_id": _STATE["kb1_id"], "tag_name": TAG_NAME},
            headers=auth_headers(admin_token),
        )
        assert_resp_error(resp, ERR_DUP_TAG)

    # ───────────────────────── AC-04: tag cascade ─────────────────────────

    async def test_ac04_tag_file_then_delete_cascades(self, client, admin_token):
        """AC-04: file carries the tag; deleting the tag unlinks it from the file."""
        headers = auth_headers(admin_token)
        file_ids = await self._resolve_files(client, admin_token)

        assert_resp_200(
            await client.post(
                "/knowledge/file/tags",
                json={
                    "knowledge_id": _STATE["kb1_id"],
                    "file_id": file_ids[FILE_A_NAME],
                    "tag_ids": [_STATE["custom_tag_id"]],
                },
                headers=headers,
            )
        )
        rows = await _file_rows(client, admin_token, _STATE["kb1_id"])
        row_a = next(r for r in rows if r["id"] == file_ids[FILE_A_NAME])
        assert any(t.get("id") == _STATE["custom_tag_id"] for t in row_a.get("tags", [])), row_a

        # Cross-KB tag id must be rejected (validation stays within the KB).
        resp = await client.post(
            "/knowledge/file/tags",
            json={
                "knowledge_id": _STATE["kb1_id"],
                "file_id": file_ids[FILE_A_NAME],
                "tag_ids": [999999999],
            },
            headers=headers,
        )
        assert_resp_error(resp, ERR_TAG_MISSING)

        # Delete → cascade removes the TagLink; the file row no longer lists it.
        assert_resp_200(
            await client.request(
                "DELETE",
                f"/knowledge/tags/{_STATE['custom_tag_id']}",
                content=json.dumps({"knowledge_id": _STATE["kb1_id"]}),
                headers={**headers, "Content-Type": "application/json"},
            )
        )
        rows = await _file_rows(client, admin_token, _STATE["kb1_id"])
        row_a = next(r for r in rows if r["id"] == file_ids[FILE_A_NAME])
        assert not any(t.get("id") == _STATE["custom_tag_id"] for t in row_a.get("tags", [])), row_a

        data = assert_resp_200(
            await client.get(f"/knowledge/{_STATE['kb1_id']}/tags", params={"page": 1, "limit": 20}, headers=headers)
        )
        assert all(t["name"] != TAG_NAME for t in data["data"])

    # ───────────────────────── AC-05..08: effective period ─────────────────────────

    async def test_ac05_set_effective_period_reflected_on_file_row(self, client, admin_token):
        """AC-05/AC-07: effective/expire dates persist on the file row (no re-parse)."""
        headers = auth_headers(admin_token)
        file_ids = await self._resolve_files(client, admin_token)
        future = _iso_date(2)

        updated = assert_resp_200(
            await client.put(
                "/knowledge/file/effective_period",
                json={
                    "knowledge_id": _STATE["kb1_id"],
                    "file_id": file_ids[FILE_B_NAME],
                    "effective_date": future,
                    "expire_date": None,
                },
                headers=headers,
            )
        )
        assert updated["effective_date"] is not None

        rows = await _file_rows(client, admin_token, _STATE["kb1_id"])
        row_b = next(r for r in rows if r["id"] == file_ids[FILE_B_NAME])
        assert row_b.get("effective_date"), "effective_date not persisted on file row"
        assert not row_b.get("expire_date"), "expire_date should be null (unlimited)"

    async def test_ac05b_effective_after_expire_rejected(self, client, admin_token):
        """Boundary: effective_date > expire_date → business 400 (HTTP 200 wrapper)."""
        file_ids = await self._resolve_files(client, admin_token)
        resp = await client.put(
            "/knowledge/file/effective_period",
            json={
                "knowledge_id": _STATE["kb1_id"],
                "file_id": file_ids[FILE_A_NAME],
                "effective_date": _iso_date(3),
                "expire_date": _iso_date(1),
            },
            headers=auth_headers(admin_token),
        )
        assert_resp_error(resp, 400)

    # ───────────────────────── AC-14 + AC-05..08: real retrieval ─────────────────────────

    @pytest.fixture(scope="class")
    async def indexed(self, shared):
        """Wait for parse/embed to finish; skip retrieval tests if unavailable."""
        ready = await _wait_files_ready(shared["client"], shared["admin_token"], _STATE["kb1_id"])
        if not ready:
            pytest.skip("file parse/embed did not finish — retrieval infra unavailable")
        return shared

    async def test_ac14_hit_test_recalls_unlimited_file(self, client, admin_token, indexed):
        """AC-14/AC-06: hit-test returns hits for the unlimited file (null dates
        = always effective) and never the future-effective file; exclusion stats
        report the not-yet-effective file."""
        headers = auth_headers(admin_token)
        file_ids = await self._resolve_files(client, admin_token)

        data = assert_resp_200(
            await client.post(f"/knowledge/{_STATE['kb1_id']}/hit_test", json={"question": QA_A}, headers=headers)
        )
        hits = data["hits"]
        assert hits, f"expected hits for {QA_A!r}, got none"
        hit_ids = {h["document_id"] for h in hits}
        assert file_ids[FILE_A_NAME] in hit_ids, hits
        assert file_ids[FILE_B_NAME] not in hit_ids, "future-effective file must not be recalled"
        for h in hits:
            assert h["knowledge_id"] == _STATE["kb1_id"]
            assert h["score"] >= 0.0
            assert h["chunk"]
        assert data["excluded"]["not_yet_effective"] >= 1, data["excluded"]

    async def test_ac05_future_effective_file_excluded(self, client, admin_token, indexed):
        """AC-05: the future-effective file never appears in hits, even for a
        question matching its content (no score_threshold → remaining corpus
        chunks may still fill top_k, but the excluded document must be absent)."""
        headers = auth_headers(admin_token)
        file_ids = await self._resolve_files(client, admin_token)
        data = assert_resp_200(
            await client.post(f"/knowledge/{_STATE['kb1_id']}/hit_test", json={"question": QA_B}, headers=headers)
        )
        hit_ids = {h["document_id"] for h in data["hits"]}
        assert file_ids[FILE_B_NAME] not in hit_ids, data["hits"]
        assert data["excluded"]["not_yet_effective"] >= 1, data["excluded"]

    async def test_ac08_expire_excludes_immediately(self, client, admin_token, indexed):
        """AC-08: setting expire_date=yesterday excludes the file on the next
        retrieval (no re-parse); clearing it restores recall."""
        headers = auth_headers(admin_token)
        file_ids = await self._resolve_files(client, admin_token)

        assert_resp_200(
            await client.put(
                "/knowledge/file/effective_period",
                json={
                    "knowledge_id": _STATE["kb1_id"],
                    "file_id": file_ids[FILE_A_NAME],
                    "effective_date": None,
                    "expire_date": _iso_date(-2),
                },
                headers=headers,
            )
        )

        data = assert_resp_200(
            await client.post(f"/knowledge/{_STATE['kb1_id']}/hit_test", json={"question": QA_A}, headers=headers)
        )
        assert data["hits"] == [], f"expired file still recalled: {data['hits']}"
        assert data["excluded"]["expired"] >= 1, data["excluded"]

        # Clear → unlimited again → recalled immediately (AC-08).
        assert_resp_200(
            await client.put(
                "/knowledge/file/effective_period",
                json={
                    "knowledge_id": _STATE["kb1_id"],
                    "file_id": file_ids[FILE_A_NAME],
                    "effective_date": None,
                    "expire_date": None,
                },
                headers=headers,
            )
        )
        data = assert_resp_200(
            await client.post(f"/knowledge/{_STATE['kb1_id']}/hit_test", json={"question": QA_A}, headers=headers)
        )
        assert any(h["document_id"] == file_ids[FILE_A_NAME] for h in data["hits"]), data["hits"]

    # ───────────────────────── AC-09/AC-10: weight/priority write path ─────────────────────────

    async def test_ac09_ac10_weight_priority_roundtrip(self, client, admin_token):
        """AC-09/AC-10 (write side): KB update API persists weight/priority and
        GET /knowledge/info returns them (retrieval ordering semantics are unit-
        tested at the shared builder; manual workflow chat in the checklist)."""
        headers = auth_headers(admin_token)

        updated = assert_resp_200(
            await client.put(
                "/knowledge/",
                json={"knowledge_id": _STATE["kb1_id"], "weight": 2.5, "priority": 1},
                headers=headers,
            )
        )
        assert updated["weight"] == 2.5
        assert updated["priority"] == 1

        info = assert_resp_200(
            await client.get("/knowledge/info", params={"knowledge_id": _STATE["kb1_id"]}, headers=headers)
        )
        kb = info[0] if isinstance(info, list) else info
        assert kb["weight"] == 2.5, kb
        assert kb["priority"] == 1, kb

        # weight=0 persists (excludes the KB from multi-KB recall — unit-tested).
        updated = assert_resp_200(
            await client.put("/knowledge/", json={"knowledge_id": _STATE["kb1_id"], "weight": 0}, headers=headers)
        )
        assert updated["weight"] == 0

    # ───────────────────────── AC-17: permission pairs ─────────────────────────

    async def test_ac17_read_permission_denied(self, client, user_token):
        """AC-17: a user without KB access cannot hit-test or list tags (403)."""
        headers = auth_headers(user_token)
        resp = await client.post(f"/knowledge/{_STATE['kb1_id']}/hit_test", json={"question": QA_A}, headers=headers)
        assert_resp_error(resp, ERR_FORBIDDEN)
        resp = await client.get(f"/knowledge/{_STATE['kb1_id']}/tags", params={"page": 1, "limit": 20}, headers=headers)
        assert_resp_error(resp, ERR_FORBIDDEN)

    async def test_ac17_write_permission_denied(self, client, admin_token, user_token):
        """AC-17: a user without KB write access cannot change effective dates
        or create tags (403, and no data is written)."""
        file_ids = await self._resolve_files(client, admin_token)
        headers = auth_headers(user_token)

        resp = await client.put(
            "/knowledge/file/effective_period",
            json={
                "knowledge_id": _STATE["kb1_id"],
                "file_id": file_ids[FILE_A_NAME],
                "effective_date": _iso_date(2),
                "expire_date": None,
            },
            headers=headers,
        )
        assert_resp_error(resp, ERR_FORBIDDEN)

        resp = await client.post(
            "/knowledge/tags",
            json={"knowledge_id": _STATE["kb1_id"], "tag_name": f"{PREFIX}越权标签"},
            headers=headers,
        )
        assert_resp_error(resp, ERR_FORBIDDEN)

        # State unchanged: file A still has no effective_date set by the denied call.
        rows = await _file_rows(client, admin_token, _STATE["kb1_id"])
        row_a = next(r for r in rows if r["id"] == file_ids[FILE_A_NAME])
        assert not row_a.get("effective_date"), "denied write must not change state"

    # ───────────────────────── error paths ─────────────────────────

    async def test_hit_test_missing_kb_returns_404(self, client, admin_token):
        """hit-test on a nonexistent KB → 404 business code."""
        resp = await client.post(
            "/knowledge/999999999/hit_test", json={"question": QA_A}, headers=auth_headers(admin_token)
        )
        assert_resp_error(resp, ERR_NOT_FOUND)
