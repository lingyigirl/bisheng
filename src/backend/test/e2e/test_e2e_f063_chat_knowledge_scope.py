"""F063 e2e: the "all organization KBs" tier, observed through a real chat turn.

Real backend on localhost:7860 (override with E2E_API_BASE) + remote middleware
(MySQL/Redis/OpenFGA/Milvus/ES/MinIO) + a configured workbench model.

WHAT THIS SUITE IS FOR — AND WHAT IT IS NOT
-------------------------------------------
The tier's rule is pinned by unit tests (`test_f063_*.py`, 26 cases) that call
`_resolve_user_kb_selection` directly with mocked DAO/service entries. This file
exists for the claims those mocks structurally CANNOT make:

  1. **Cross-entry agreement (AC-10).** "The set the 「全部」tier resolves" must
     equal "the set the LIST entry reports" — `GET /knowledge?type=0`. In unit
     tests both sides are the same mock, so the assertion is a tautology; only a
     real request proves the production call chains agree. This is the F062
     lesson: the candidate-set defect there was invisible to the rule-level
     unit tests and only a real request surfaced it.

  2. **Narrowing is never widened by the tier (AC-11/AC-12).** The expansion is
     the one place in this feature where a bug leaks data: it feeds the prompt's
     <selected_knowledge_bases> section and the `search_knowledge_bases`
     whitelist. A user with no grants must resolve to nothing, and the tier must
     not be a way around `use_kb`.

  3. **The >50 server limit still bites end-to-end (AC-09).** The feature stops
     the CLIENT from enumerating; "no longer triggered" is not "removed".

HOW THE RESOLVED SET IS OBSERVED
--------------------------------
It is internal — nothing in the HTTP response carries it. The observable is the
INFO line the funnel itself emits (chat_service.py):

    [resolve_user_kb] resolved 19 kbs ids=[145, 144, ...] tag_counts=[...]

plus, for the narrowing step, `[perf][knowledge.permission_map] ... candidates=N
kept=M`. The suite tails the backend's own log file — the same "后端日志对照"
the feature's tasks.md asks for. Log path: `data/bisheng.log` relative to the
repo root (config.yaml `logger_conf`), override with E2E_BACKEND_LOG.

Because the funnel runs BEFORE the agent loop, the line is emitted even if the
model later fails — but the turn still has to get past `_agent_initialize_chat`,
which needs a valid model, so every case that reads the log is `needs_llm`.

WHAT RUNS WITHOUT A MODEL
-------------------------
`no_llm` cases are pure API contracts: the >50 rejection, and the AC-12 property
that neither tier's request mutates anyone's visibility. `needs_llm` cases skip
LOUDLY (never pass vacuously) when no workbench model is configured — the F061
precedent.

REQUIRED FIXTURE
----------------
The narrowing case needs a user with NO grants. There is no user-delete endpoint
in this codebase, and the F062 suite has the same constraint, so the convention
is followed: one `e2e-f063-user-*` account is created and left behind. It is
granted nothing, so a leftover can never widen anyone's visibility — and it is
also the very state the case asserts on.
"""

import os
import pathlib
import re
import time
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE
from test.e2e.helpers.auth import auth_headers, encrypt_password

pytestmark = pytest.mark.e2e

CHAT_URL = f"{API_BASE}/workstation/chat/completions"
KNOWLEDGE_URL = f"{API_BASE}/knowledge"
DEPT_STATS_URL = f"{API_BASE}/knowledge/department_stats"

# Everything this suite creates is prefixed so a re-run and a parallel suite
# never collide, and so cleanup can be scoped to its own data (>=5 chars).
PREFIX = "e2e-f063-"
USER_PASSWORD = "F063_e2e_pw_1"

# KnowledgeTypeEnum: NORMAL=0 (organization doc library — the only type the tier
# expands), SPACE=3. Spelled out rather than derived, for the reason F062's
# suite records: these numbers have drifted in a sibling suite before.
TYPE_NORMAL = 0
TYPE_SPACE = 3

# `GET /knowledge` defaults to page_size=10 and this deployment has more
# libraries than that, so a small page silently truncates a set comparison.
_PAGE_SIZE = 200

# The funnel resolves the set BEFORE the agent loop, so the log line lands within
# a few hundred ms of the request; the model then streams for seconds. Polling
# for the line keeps the read deterministic without waiting the stream out.
_LOG_POLL_TIMEOUT_S = 20.0
_LOG_POLL_INTERVAL_S = 0.25

_HEARTBEAT = "好"


# ---------------------------------------------------------------------------
# Auth / HTTP plumbing (same shape as the F057 / F062 suites)
# ---------------------------------------------------------------------------


def _login(client: httpx.Client, username: str = "admin", password: str | None = None) -> str:
    if password is None:
        password = os.environ.get("E2E_ADMIN_PASSWORD", "admin123")
    pem = client.get(f"{API_BASE}/user/public_key").json()["data"]["public_key"]
    resp = client.post(
        f"{API_BASE}/user/login",
        json={"user_name": username, "password": encrypt_password(password, pem)},
    )
    body = resp.json()
    assert body["status_code"] == 200, f"Login failed for {username}: {body.get('status_message')}"
    return body["data"]["access_token"]


def _encrypt(client: httpx.Client, password: str) -> str:
    pem = client.get(f"{API_BASE}/user/public_key").json()["data"]["public_key"]
    return encrypt_password(password, pem)


def _ok(resp: httpx.Response):
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert body["status_code"] == 200, f"Business error {body['status_code']}: {body.get('status_message')}"
    return body.get("data")


def _rows(payload):
    """`GET /knowledge` answers with {data: [...]} or a bare list, depending."""
    if isinstance(payload, dict):
        return payload.get("data", [])
    return payload or []


# ---------------------------------------------------------------------------
# The observation lever: the backend's own log
# ---------------------------------------------------------------------------


def _log_path() -> pathlib.Path:
    override = os.environ.get("E2E_BACKEND_LOG")
    if override:
        return pathlib.Path(override)
    # test/e2e/this_file.py -> <repo>/src/backend/data/bisheng.log
    return pathlib.Path(__file__).resolve().parents[2] / "data" / "bisheng.log"


def _log_offset() -> int:
    """Byte offset to read forward from. Missing log => read from 0."""
    p = _log_path()
    return p.stat().st_size if p.exists() else 0


def _read_since(offset: int) -> str:
    """Everything appended since `offset`, tolerating a rotation in between.

    The sink rotates at 00:00 into a dated sibling, so a long-running suite can
    see the live file shorter than the recorded offset. Restarting from 0 reads
    a little history back rather than silently returning nothing.
    """
    p = _log_path()
    if not p.exists():
        return ""
    with p.open("r", encoding="utf-8", errors="replace") as f:
        if p.stat().st_size < offset:
            offset = 0
        f.seek(offset)
        return f.read()


_RESOLVE_RE = re.compile(r"\[resolve_user_kb\] resolved (\d+) kbs ids=\[([^\]]*)\]")
_PERF_RE = re.compile(r"\[perf\]\[knowledge\.permission_map\][^\n]*?candidates=(\d+) kept=(\d+)")


def _wait_for(predicate, offset: int, what: str):
    """Poll the log for a match; fail with the log's own tail if it never lands."""
    deadline = time.monotonic() + _LOG_POLL_TIMEOUT_S
    seen = ""
    while time.monotonic() < deadline:
        seen = _read_since(offset)
        hit = predicate(seen)
        if hit is not None:
            return hit
        time.sleep(_LOG_POLL_INTERVAL_S)
    raise AssertionError(f"No {what} in the backend log within {_LOG_POLL_TIMEOUT_S}s. Log tail:\n{seen[-2000:]}")


def _resolved_since(offset: int) -> tuple[int, list[int]]:
    def parse(text):
        matches = _RESOLVE_RE.findall(text)
        if not matches:
            return None
        count, ids = matches[-1]
        return int(count), [int(x) for x in ids.split(",") if x.strip()]

    return _wait_for(parse, offset, "[resolve_user_kb] resolved line")


def _permission_map_since(offset: int) -> tuple[int, int]:
    """(candidates, kept) for the last `use_kb` narrowing in this window."""

    def parse(text):
        matches = _PERF_RE.findall(text)
        return (int(matches[-1][0]), int(matches[-1][1])) if matches else None

    return _wait_for(parse, offset, "[perf][knowledge.permission_map] line")


# ---------------------------------------------------------------------------
# Driving one turn
# ---------------------------------------------------------------------------


def _chat_payload(model: str, use_knowledge_base: dict | None, text: str = _HEARTBEAT) -> dict:
    body = {
        "clientTimestamp": str(int(time.time())),
        "model": model,
        "text": text,
        "tools": [],
    }
    if use_knowledge_base is not None:
        body["use_knowledge_base"] = use_knowledge_base
    return body


def _send_turn(client: httpx.Client, token: str, model: str, use_knowledge_base: dict | None) -> str:
    """Send one real turn, drain the stream, and return its FIRST event line.

    The stream is drained rather than sampled, and that is not tidiness: the
    funnel runs AFTER the first few events are emitted (user message, processing
    begin, question over — see chat_service.py), so leaving the `with` after
    reading one line tears the generator down before the resolution happens. The
    log line would never appear and a perfectly correct implementation would look
    broken.

    The answer text is deliberately not asserted on: that would be asserting on
    the model's phrasing, which F061's suite records as a bad trade.
    """
    with client.stream(
        "POST", CHAT_URL, json=_chat_payload(model, use_knowledge_base), headers=auth_headers(token), timeout=180
    ) as resp:
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
        lines = resp.iter_lines()
        first = next(lines, "")
        for _ in lines:
            pass
    return first


def _drive_turn(
    client: httpx.Client, token: str, model: str, use_knowledge_base: dict | None
) -> tuple[str, int, list[int]]:
    """One turn, plus the resolved (count, ids) the funnel logged for it."""
    offset = _log_offset()
    first = _send_turn(client, token, model, use_knowledge_base)
    count, ids = _resolved_since(offset)
    return first, count, ids


def _first_space_id(client: httpx.Client, token: str) -> int | None:
    """A real knowledge-space id, discovered rather than hardcoded.

    Space ids differ per deployment, and a space is needed to make the "no
    organization library" cases observable at all: with nothing selected the
    funnel returns before it logs (chat_service.py's `if not ids: return []`), so
    a space is what turns "resolved nothing extra" into a positive assertion.
    """
    spaces = _rows(
        _ok(
            client.get(
                KNOWLEDGE_URL,
                params={"page_size": _PAGE_SIZE, "type": TYPE_SPACE},
                headers=auth_headers(token),
            )
        )
    )
    return int(spaces[0]["id"]) if spaces else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=180) as c:
        assert c.get(f"{API_BASE}/env").status_code == 200, "Backend not reachable at " + API_BASE
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    return _login(client)


@pytest.fixture(scope="module")
def model_id(client, admin_token) -> str:
    """The workbench model the daily chat entry uses.

    Skips the `needs_llm` cases rather than failing them: an environment with the
    middleware but no model is a legitimate configuration (the F061 precedent),
    and a silent pass would be worse than a loud skip.
    """
    cfg = _ok(client.get(f"{API_BASE}/llm/workbench", headers=auth_headers(admin_token)))
    cfg = cfg.get("data", cfg) if isinstance(cfg, dict) else cfg
    models = (cfg or {}).get("models") or []
    if not models:
        pytest.skip("no workbench model configured — needs_llm cases cannot run")
    return str(models[0]["id"])


@pytest.fixture(scope="module")
def ungranted_user(client, admin_token) -> tuple[str, str]:
    """A non-admin with NO grants: (username, token).

    This is the state the narrowing case asserts on, and it is also why leaving
    the account behind is safe — it can see nothing, so it cannot widen anyone's
    visibility. See the module docstring for why it is not deleted.
    """
    username = f"{PREFIX}user-{uuid.uuid4().hex[:8]}"
    _ok(
        client.post(
            f"{API_BASE}/user/create",
            json={"user_name": username, "password": _encrypt(client, USER_PASSWORD)},
            headers=auth_headers(admin_token),
        )
    )
    return username, _login(client, username, USER_PASSWORD)


def _visible_org_ids(client: httpx.Client, token: str) -> set[int]:
    """The caller's organization libraries, as the LIST entry reports them."""
    data = _ok(
        client.get(
            KNOWLEDGE_URL,
            params={"page_size": _PAGE_SIZE, "type": TYPE_NORMAL},
            headers=auth_headers(token),
        )
    )
    return {int(r["id"]) for r in _rows(data)}


def _dept_badges(client: httpx.Client, token: str) -> dict:
    data = _ok(client.get(DEPT_STATS_URL, headers=auth_headers(token)))
    rows = data if isinstance(data, list) else _rows(data)
    return {int(r["department_id"]): int(r["count"]) for r in rows}


# ---------------------------------------------------------------------------
# no_llm — API contracts that need no model
# ---------------------------------------------------------------------------


def test_ac09_more_than_fifty_ids_are_still_rejected(client, admin_token):
    """AC-09: the server-side 50 limit survives the feature.

    The feature makes the client stop enumerating, so this validator is no longer
    reached in practice — that is not the same as removed, and the request must
    still not run. The `knowledge_space_ids` sibling is checked in the same test
    because it is the UNTOUCHED control: if it ever stops failing here, the
    failure is in this deployment, not in the feature.

    On the response shape: a `field_validator` raising ValueError makes pydantic
    attach the exception OBJECT to the error's `ctx`, and the deployment's
    `handle_request_validation_error` (main.py) hands `exc.errors()` straight to
    JSONResponse — which cannot serialise it, so the generic handler answers with
    `{status_code: 500, status_message: "Object of type ValueError is not JSON
    serializable"}`. Pre-existing, generic to every such validator in the
    codebase, and out of this feature's scope — so the assertion is the property
    that matters (the turn does not run) rather than the shape.
    """
    body = _chat_payload("1", {"organization_knowledge_ids": list(range(1, 52))})
    resp = client.post(CHAT_URL, json=body, headers=auth_headers(admin_token))
    assert resp.status_code == 200  # the deployment wraps errors in a 200 envelope
    assert resp.json()["status_code"] != 200, (
        "a 51-id organization list was ACCEPTED — the >50 limit is gone, not merely unused"
    )

    sibling = _chat_payload("1", {"knowledge_space_ids": list(range(1, 52))})
    sibling_resp = client.post(CHAT_URL, json=sibling, headers=auth_headers(admin_token))
    assert sibling_resp.json()["status_code"] != 200, (
        "the untouched knowledge_space_ids validator stopped rejecting at 51 — environment problem, "
        "not a feature problem"
    )


def test_ac12_a_none_tier_turn_leaves_every_visibility_entry_untouched(client, admin_token):
    """AC-12: "不使用" is a scope choice for the turn, not an authorization change.

    Written as "must be unchanged", not "still contains": the point is the
    ABSENCE of a negative-authorization mechanism, and a positive assertion
    cannot show absence. Both the platform-side list and the department badges
    are read, because a narrowing could plausibly land on either.
    """
    before_list = _visible_org_ids(client, admin_token)
    before_badges = _dept_badges(client, admin_token)

    # The exact shape the client sends for the 「不使用」tier: nothing selected,
    # the tier flag false. No model needed — the request only has to be served.
    first = _send_turn(client, admin_token, "1", {"organization_knowledge_all": False})
    assert "error" not in first, f"the none-tier turn was rejected before it ran: {first[:300]}"

    assert _visible_org_ids(client, admin_token) == before_list
    assert _dept_badges(client, admin_token) == before_badges


# ---------------------------------------------------------------------------
# needs_llm — the resolved set, observed through a real turn
# ---------------------------------------------------------------------------


@pytest.mark.needs_llm
def test_ac03_ac10_all_tier_resolves_exactly_the_list_entry(client, admin_token, model_id):
    """AC-03 + AC-10: 「全部」 == every library the LIST entry reports.

    This is the claim unit tests cannot make (both sides would be the same mock):
    the expansion picks its candidates and applies its narrowing on the PRODUCTION
    chains, and the result has to agree with what the platform list shows the same
    user. A symmetric-difference report is used rather than set equality so a
    failure names the libraries that disagree, which is what a reviewer needs.
    """
    listed = _visible_org_ids(client, admin_token)
    assert listed, "no organization libraries in this deployment — the case would pass vacuously"

    _, _, resolved = _drive_turn(client, admin_token, model_id, {"organization_knowledge_all": True})

    assert set(resolved) == listed, (
        f"all tier disagrees with the list entry: "
        f"extra={sorted(set(resolved) - listed)} missing={sorted(listed - set(resolved))}"
    )


@pytest.mark.needs_llm
def test_ac01_all_tier_stacked_with_a_space_keeps_both(client, admin_token, model_id):
    """AC-01: the two are independent axes — 「全部」 plus a space is legal.

    The space is DISCOVERED from the deployment rather than hardcoded: a real
    space id is needed to assert `source` routing, and the dev database's ids
    differ from any other environment's.
    """
    space_id = _first_space_id(client, admin_token)
    if space_id is None:
        pytest.skip("no knowledge space in this deployment to stack onto the all tier")

    listed = _visible_org_ids(client, admin_token)
    _, _, resolved = _drive_turn(
        client, admin_token, model_id, {"organization_knowledge_all": True, "knowledge_space_ids": [space_id]}
    )

    assert space_id in resolved, f"the selected space {space_id} is missing from {resolved}"
    assert listed.issubset(set(resolved)), (
        f"all tier lost organization libraries when a space was stacked: {sorted(listed - set(resolved))}"
    )


@pytest.mark.needs_llm
def test_ac04_ac06_none_tier_resolves_the_space_only(client, admin_token, model_id):
    """AC-04 + AC-06: 「不使用」 resolves the selected space and NO organization library.

    A space is used as the carrier because the funnel returns before it logs when
    nothing at all is selected (`if not ids: return []`) — so "resolved nothing
    extra" is only observable as a positive fact when something IS selected. That
    makes this the stronger assertion of the two: not "no line appeared", but
    "the space came through and not one of the N organization libraries did".

    An admin is the interesting caller here: they skip the per-KB verdict, so an
    implementation that expanded and filtered later would still look right for a
    regular user. Only checking the tier distinguishes them.
    """
    space_id = _first_space_id(client, admin_token)
    if space_id is None:
        pytest.skip("no knowledge space in this deployment to carry the none-tier case")

    listed = _visible_org_ids(client, admin_token)
    assert listed, "no organization libraries in this deployment — the case would pass vacuously"

    _, _, resolved = _drive_turn(
        client, admin_token, model_id, {"organization_knowledge_all": False, "knowledge_space_ids": [space_id]}
    )
    assert space_id in resolved, f"the selected space {space_id} is missing from {resolved}"
    assert not (set(resolved) & listed), (
        f"「不使用」 still pulled in organization libraries: {sorted(set(resolved) & listed)}"
    )


@pytest.mark.needs_llm
def test_ac11_the_tier_never_widens_past_a_users_grants(client, ungranted_user, model_id):
    """AC-11 + AC-12: the expansion is narrowed by `use_kb`, and a user with no
    grants gets nothing.

    The security-relevant case. A user with zero libraries visible in the LIST
    entry must also resolve to zero through 「全部」 — anything else means the tier
    is a way around the permission check, and the result goes straight into the
    prompt and the search whitelist.

    The `permission_map` line is asserted too, because the two can disagree: a
    `resolved 0` with `candidates=0` would mean the candidate query found nothing,
    which is a different (and much less reassuring) fact than "19 candidates were
    offered and all 19 were correctly withheld".
    """
    _, token = ungranted_user
    listed = _visible_org_ids(client, token)
    assert listed == set(), (
        f"fixture user unexpectedly sees {sorted(listed)} — the account is left behind between runs "
        f"and must stay grant-free for this case to mean anything"
    )

    offset = _log_offset()
    _first, count, resolved = _drive_turn(client, token, model_id, {"organization_knowledge_all": True})
    assert count == 0 and resolved == [], (
        f"an ungranted user resolved {count} organization libraries through the all tier: {resolved}"
    )

    candidates, kept = _permission_map_since(offset)
    assert kept == 0, f"the narrowing kept {kept} of {candidates} candidates for an ungranted user"
    assert candidates > 0, (
        'the expansion found no candidates at all, so "kept 0" proves nothing — either this deployment '
        "has no organization libraries or the candidate query is broken"
    )


@pytest.mark.needs_llm
def test_legacy_shapes_are_untouched_by_the_tier(client, admin_token, model_id):
    """AC-03/AC-25: an old client's body still resolves exactly its own ids.

    The field is absent entirely (not merely false) — that is the shape a client
    predating the feature sends, and its meaning must not drift: the explicit path
    reads the ids it was given, in order, and never expands. The unit suite pins
    this at the function level; here it is pinned through the real request schema
    and the real retrieval path, which is where a default that flipped would show
    up as a scope change for every existing caller.
    """
    both = sorted(_visible_org_ids(client, admin_token))
    if len(both) < 2:
        pytest.skip('need at least two organization libraries to distinguish "explicit" from "all"')
    explicit = both[:2]

    _, _, resolved = _drive_turn(client, admin_token, model_id, {"organization_knowledge_ids": explicit})
    assert resolved == explicit, f"the legacy explicit path resolved {resolved}, expected exactly {explicit}"

    # The flag explicitly false must be indistinguishable from the flag absent:
    # if the two ever diverge, the field's default has stopped meaning "off".
    _, _, resolved_false = _drive_turn(
        client, admin_token, model_id, {"organization_knowledge_ids": explicit, "organization_knowledge_all": False}
    )
    assert resolved_false == resolved, (
        f"absent={resolved} vs explicit-false={resolved_false} — absent and False are no longer the same value"
    )


@pytest.mark.needs_llm
def test_an_all_tier_body_is_accepted_and_the_turn_runs(client, admin_token, model_id):
    """AC-03's payload contract: the new field parses at the API boundary.

    Kept separate from the resolution cases because it fails for a different
    reason and a different person fixes it: a 422 here means the request schema
    drifted (a new required field, a tightened validator), which no assertion
    about the resolved set would localise. The sibling of the >50 case: that one
    proves the limit still bites, this one proves the new tier passes through.
    """
    first, _count, resolved = _drive_turn(client, admin_token, model_id, {"organization_knowledge_all": True})
    assert "error" not in first, f"the all-tier body was rejected before the funnel ran: {first[:300]}"
    assert resolved, "the all-tier body was accepted but the funnel resolved nothing"
