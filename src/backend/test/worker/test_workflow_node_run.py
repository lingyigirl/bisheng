"""F052: node-run payload sanitization and best-effort insert behavior."""

from bisheng.database.models.workflow_node_run import (
    MAX_PAYLOAD_BYTES,
    NodeRunDao,
    _ensure_payload_size,
    _prepare_payload,
    _sanitize_payload,
)


def test_sanitize_scalars_pass_through():
    assert _sanitize_payload(None) is None
    assert _sanitize_payload(42) == 42
    assert _sanitize_payload(True) is True
    assert _sanitize_payload("ok") == "ok"


def test_sanitize_bytes_becomes_summary():
    assert _sanitize_payload(b"\x00\x01binary") == "<bytes 8>"
    assert _sanitize_payload(bytearray(4)) == "<bytes 4>"
    assert _sanitize_payload(memoryview(b"abc")) == "<bytes 3>"


def test_sanitize_long_string_truncated():
    long = "x" * 100_000
    out = _sanitize_payload(long)
    assert len(out) < len(long)
    assert out.endswith("…<truncated>")


def test_sanitize_nested_structures_capped():
    value = {"a": [1, 2, {"b": b"raw"}], "c": list(range(1000))}
    out = _sanitize_payload(value)
    assert out["a"][2]["b"] == "<bytes 3>"
    assert len(out["c"]) == 101  # 100 items + truncation marker
    assert "more items" in out["c"][-1]


def test_sanitize_unserializable_falls_back():
    class Weird:
        def __str__(self):
            raise RuntimeError("nope")

    assert _sanitize_payload(Weird()) == "<unserializable>"
    assert _sanitize_payload({"k": Weird()}) == {"k": "<unserializable>"}


def test_sanitize_depth_capped():
    nested = cur = {}
    for _ in range(20):
        cur["next"] = {}
        cur = cur["next"]
    out = _sanitize_payload(nested)
    for _ in range(9):
        out = out["next"]
    assert out == "<max depth>"


def test_prepare_payload_wraps_non_dict():
    assert _prepare_payload(None) is None
    assert _prepare_payload(123) == {"value": 123}
    assert _prepare_payload({"k": "v"}) == {"k": "v"}


def test_oversized_payload_replaced_with_marker():
    big = {"data": "x" * (MAX_PAYLOAD_BYTES + 100)}
    assert _ensure_payload_size(big) == {"_truncated": True, "reason": f"payload exceeded {MAX_PAYLOAD_BYTES} bytes"}
    small = {"k": "v"}
    assert _ensure_payload_size(small) == small
    assert _ensure_payload_size(None) is None


def test_insert_event_never_raises_on_db_failure(monkeypatch):
    """AC-04: persistence failure must not break the workflow run."""
    from bisheng.database.models import workflow_node_run as wnr

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(wnr, "get_sync_db_session", boom)
    # must return without raising
    assert (
        NodeRunDao.insert_event(
            unique_id="run-1",
            exec_id="exec-1",
            chat_id="chat-1",
            flow_id="flow-1",
            node_id="node-1",
            node_name="节点",
            event_type="start",
            status="running",
            user_id=1,
            tenant_id=1,
        )
        is None
    )
