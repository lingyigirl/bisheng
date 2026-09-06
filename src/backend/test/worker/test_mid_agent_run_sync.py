"""F046: mid_agent_run_dtl sync maps APPLICATION_PROCESS events to rows idempotently."""

from datetime import datetime, timedelta

from bisheng.worker.telemetry import mid_table as mt


def _fake_event(event_id, status=None):
    return {
        "_id": f"doc_{event_id}",
        "_source": {
            "event_id": event_id,
            "timestamp": 1750000000,
            "user_context": {"user_id": 1, "user_name": "admin"},
            "event_data": {
                "application_process_app_id": "flow-1",
                "application_process_app_name": "会议纪要智能助手",
                "application_process_app_type": "workflow",
                "application_process_chat_id": "chat-1",
                "application_process_process_time": 1234,
                **({"application_process_status": status} if status else {}),
            },
        },
    }


def test_sync_maps_events_and_is_idempotent(monkeypatch):
    events = [_fake_event("evt-1", "success"), _fake_event("evt-2")]
    inserted = []

    class FakeAgentRun:
        def get_records_by_time_range_sync(self, **kwargs):
            page = kwargs.get("page", 1)
            return events if page == 1 else None

        def insert_records_sync(self, records):
            inserted.extend(records)

    monkeypatch.setattr(mt, "AgentRun", FakeAgentRun)
    monkeypatch.setattr(mt, "get_yesterday_date_range",
                        lambda mid_table, start_date, end_date: (datetime(2025, 1, 1), datetime(2025, 1, 2)))

    # mock_services fixture may wrap the task in a non-callable _FakeCeleryTask
    # exposing .run — invoke the real function body either way.
    sync_fn = mt.sync_mid_agent_run_dtl
    (sync_fn.run if hasattr(sync_fn, "run") and not callable(sync_fn) else sync_fn)()

    assert len(inserted) == 2
    first = inserted[0]
    assert first.es_id == "doc_evt-1"
    assert first.event_id == "evt-1"
    assert first.app_id == "flow-1"
    assert first.app_type == "workflow"
    assert first.process_time == 1234
    assert first.status == "success"
    # historical event without status -> None (excluded from success rate)
    assert inserted[1].status is None
