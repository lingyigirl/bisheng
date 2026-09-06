"""F046: application_process telemetry events must carry a prefixed status field."""

from bisheng.common.constants.enums.telemetry import (
    ApplicationTypeEnum,
    BaseTelemetryTypeEnum,
    StatusEnum,
)
from bisheng.common.schemas.telemetry.base_telemetry_schema import BaseTelemetryEvent, UserContext
from bisheng.common.schemas.telemetry.event_data_schema import ApplicationProcessEventData


def test_application_process_model_dump_contains_prefixed_status():
    event = BaseTelemetryEvent(
        event_type=BaseTelemetryTypeEnum.APPLICATION_PROCESS,
        user_context=UserContext(user_id=1, user_name="admin"),
        event_data=ApplicationProcessEventData(
            app_id="flow-1",
            app_name="测试流程",
            app_type=ApplicationTypeEnum.WORKFLOW,
            chat_id="chat-1",
            start_time=1000,
            end_time=1500,
            process_time=500,
            status=StatusEnum.SUCCESS,
        ),
    )
    dumped = event.model_dump()
    assert dumped["event_data"]["application_process_status"] == "success"


def test_application_process_status_accepts_failed():
    data = ApplicationProcessEventData(
        app_id="flow-1",
        app_name="测试流程",
        app_type=ApplicationTypeEnum.WORKFLOW,
        chat_id=None,
        start_time=1000,
        end_time=1500,
        process_time=500,
        status=StatusEnum.FAILED,
    )
    assert data.status == StatusEnum.FAILED
