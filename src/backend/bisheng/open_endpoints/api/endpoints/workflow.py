import copy
import time
import uuid
from uuid import UUID

from fastapi import APIRouter, Body, Path, Request, WebSocket, WebSocketException
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.responses import StreamingResponse

from bisheng.api.services.guardrail import build_input_guard, build_output_guard
from bisheng.api.services.workflow import WorkFlowService
from bisheng.api.v1.chat import chat_manager
from bisheng.api.v1.schema.workflow import WorkflowEvent, WorkflowEventType, WorkflowStream
from bisheng.api.v1.schemas import resp_200
from bisheng.common.chat.types import WorkType
from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum, BaseTelemetryTypeEnum
from bisheng.common.errcode.http_error import NotFoundError, ServerError
from bisheng.common.errcode.open_api import OpenApiNotFoundError, OpenApiToolScopeNotApplicableError
from bisheng.common.schemas.telemetry.event_data_schema import ApplicationAliveEventData
from bisheng.common.services import telemetry_service
from bisheng.core.logger import trace_id_var
from bisheng.database.models.flow import FlowDao, FlowType
from bisheng.open_api.domain.scopes import open_api_scope
from bisheng.open_endpoints.domain.utils import (
    _is_key_authenticated_call,
    assert_open_api_app_visible,
    get_default_operator,
    get_default_operator_async,
)
from bisheng.worker.workflow.redis_callback import RedisCallback
from bisheng.worker.workflow.tasks import continue_workflow, execute_workflow, workflow_stateful_worker
from bisheng.workflow.common.workflow import WorkflowStatus

router = APIRouter(prefix="/workflow", tags=["OpenAPI", "Workflow"])


@open_api_scope("workflow:invoke")
@router.post("/invoke")
async def invoke_workflow(
    request: Request,
    workflow_id: UUID = Body(..., description="Workflow UniqueID"),
    override: dict | None = Body(default=None, description="override node params"),
    tools: list[dict] | None = Body(
        default=None,
        description="F076: 本轮启用哪些工具(白名单,限于该应用 agent 节点已绑定的工具);不传=应用默认",
    ),
    stream: bool | None = Body(default=True, description="Whether to stream calls"),
    user_input: dict | None = Body(default=None, description="User input", alias="input"),
    message_id: int | None = Body(default=None, description="MessageID,Once,Unique identifier of user input message"),
    session_id: str | None = Body(default=None, description="会话ID,一次workflow调用的唯一标识"),
):
    login_user = get_default_operator()
    workflow_id = workflow_id.hex
    # Query workflow information
    workflow_info = await FlowDao.aget_flow_by_id(workflow_id)
    if not workflow_info:
        raise NotFoundError.http_exception()
    if workflow_info.flow_type != FlowType.WORKFLOW.value:
        raise NotFoundError.http_exception()
    # F076 T-29(AC-23):密钥调用时,目标流程必须在调用身份的可见范围内,否则
    # 404/11809 —— 与"不存在"不可区分(防枚举)。非密钥调用保持旧行为(AC-21)。
    await assert_open_api_app_visible(workflow_id, login_user)

    # Resolve Outchat_idAndunique_id
    if not session_id:
        chat_id = uuid.uuid4().hex
        unique_id = f"{chat_id}_async_task_id"
        session_id = unique_id
    else:
        chat_id = session_id.split("_", 1)[0]
        unique_id = session_id
    # F076 T-18:`tools[]` 校验(白名单=全部 agent 节点 tool_list 的并集);
    # 不传返回 None,行为与升级前一致。
    from bisheng.open_api.domain.schemas.tools import parse_tool_selection
    from bisheng.open_api.domain.services import tool_scope_service

    tool_override = tool_scope_service.resolve_workflow_tool_override(
        workflow_info.data, parse_tool_selection(tools)
    )

    start_time = time.time()
    logger.debug(f"invoke_workflow: {workflow_id}, {session_id}")
    workflow = RedisCallback(unique_id, workflow_id, chat_id, login_user.user_id, source="api")

    execute_worker = await workflow_stateful_worker.find_task_node(chat_id)
    # Query workflow status
    status_info = workflow.get_workflow_status()
    if not status_info:
        # Initialize workflow
        # F076 T-18:tools[] 编译成 {agent 节点 -> tool_list} 的 override 片段,
        # 与调用方自己的 override 合并(节点内以 tools 的覆写为准——它更具体)。
        data_for_run = workflow_info.data
        if tool_override is not None:
            merged = dict(override or {})
            for node_id, patch in tool_override.items():
                merged[node_id] = {**(merged.get(node_id) or {}), **patch}
            override = merged
            # override_nodes_params 是原地改写(worker/workflow/redis_callback.py):
            # 传副本,避免把 FlowDao 取出的 ORM JSON 字段写脏。
            data_for_run = copy.deepcopy(workflow_info.data)
        workflow.set_workflow_data(data_for_run, override=override)
        workflow.set_workflow_status(WorkflowStatus.WAITING.value)
        # Start asynchronous task
        execute_workflow.apply_async([unique_id, workflow_id, chat_id, login_user.user_id, "api"], queue=execute_worker)
    elif status_info and tool_override is not None:
        # F076 T-18 / INV-24:续跑与运行中的会话不再写执行数据,override 无处施加。
        # 静默忽略"本轮工具"比报错更危险——调用方会以为限定了却没限定。
        raise OpenApiToolScopeNotApplicableError()
    elif status_info["status"] == WorkflowStatus.INPUT.value:
        if not user_input:
            raise ServerError(msg="workflow waiting for user input, but user input not provided")
        # Set user input
        if not message_id:
            raise ServerError(msg="message_id is required when providing user input")
        await workflow.async_set_user_input(user_input, message_id, verify_input=True)
        await workflow.async_set_workflow_status(WorkflowStatus.INPUT_OVER.value)
        continue_workflow.apply_async(
            [unique_id, workflow_id, chat_id, login_user.user_id, "api"], queue=execute_worker
        )

    logger.debug(f"waiting workflow over or input: {workflow_id}, {session_id}")

    async def handle_workflow_event(event_list: list):
        async for event in workflow.get_response_until_break():
            if event.category == WorkflowEventType.NodeRun.value:
                continue
            # Non-streaming requests, filtering out streaming output events generated by nodes
            if not stream and event.category == WorkflowEventType.StreamMsg.value and event.type == "stream":
                continue
            workflow_stream = WorkflowStream(
                session_id=session_id, data=WorkFlowService.convert_chat_response_to_workflow_event(event)
            )
            event_list.append(workflow_stream.data)
            yield f"data: {workflow_stream.model_dump_json()}\n\n"
        tmp_status_info = await workflow.async_get_workflow_status()
        if tmp_status_info["status"] in [WorkflowStatus.SUCCESS.value, WorkflowStatus.FAILED.value]:
            await workflow.async_clear_workflow_status()
        if tmp_status_info["status"] == WorkflowStatus.SUCCESS.value:
            workflow_stream = WorkflowStream(
                session_id=session_id, data=WorkflowEvent(event=WorkflowEventType.Close.value)
            )
            event_list.append(workflow_stream.data)
            yield f"data: {workflow_stream.model_dump_json()}\n\n"

    res = []
    # Non-streaming returns a cumulative list of events
    if not stream:
        async for _ in handle_workflow_event(res):
            pass
        end_time = time.time()
        await telemetry_service.log_event(
            user_id=login_user.user_id,
            event_type=BaseTelemetryTypeEnum.APPLICATION_ALIVE,
            trace_id=trace_id_var.get(),
            event_data=ApplicationAliveEventData(
                app_id=workflow_id,
                app_name=workflow_info.name,
                app_type=ApplicationTypeEnum.WORKFLOW,
                chat_id=session_id,
                start_time=int(start_time),
                end_time=int(end_time),
            ),
        )
        return resp_200(data={"session_id": session_id, "events": res})
    try:
        return StreamingResponse(handle_workflow_event(res), media_type="text/event-stream")
    except Exception as exc:
        logger.exception(f"invoke_workflow error: {exc!s}")
        return JSONResponse(status_code=500, content=str(exc))
    finally:
        end_time = time.time()
        await telemetry_service.log_event(
            user_id=login_user.user_id,
            event_type=BaseTelemetryTypeEnum.APPLICATION_ALIVE,
            trace_id=trace_id_var.get(),
            event_data=ApplicationAliveEventData(
                app_id=workflow_id,
                app_name=workflow_info.name,
                app_type=ApplicationTypeEnum.WORKFLOW,
                chat_id=session_id,
                start_time=int(start_time),
                end_time=int(end_time),
            ),
        )


@open_api_scope("workflow:invoke")
@router.post("/stop")
async def stop_workflow(
    request: Request,
    workflow_id: UUID = Body(..., description="Workflow UniqueID"),
    session_id: str = Body(description="SessionsID,Once,workflowUnique identifier of the call"),
):
    workflow_id = workflow_id.hex
    login_user = await get_default_operator_async()
    # F076 T-29(AC-23):停止同样要按可见性收口——原先连存在性都不查,任意 id 都能停。
    if _is_key_authenticated_call():
        if not await FlowDao.aget_flow_by_id(workflow_id):
            raise OpenApiNotFoundError()
        await assert_open_api_app_visible(workflow_id, login_user)
    chat_id = session_id.split("_", 1)[0]
    unique_id = session_id
    workflow = RedisCallback(unique_id, workflow_id, chat_id, login_user.user_id, source="api")
    await workflow.async_set_workflow_stop()
    return resp_200()


@open_api_scope("workflow:invoke", guest=True)
@router.websocket("/chat/{workflow_id}")
async def workflow_ws(
    *, workflow_id: UUID = Path(..., description="Workflow UniqueID"), websocket: WebSocket, chat_id: str | None = None
):
    """Use Exempt Login Link"""
    try:
        workflow_id = workflow_id.hex
        # Authorize.jwt_required(auth_from='websocket', websocket=websocket)
        # payload = Authorize.get_jwt_subject()
        login_user = await get_default_operator_async()
        # F076 T-29(AC-23):连接期就按可见性收口(原先只在逐条消息时检查)。
        # WS 没有 HTTP 状态,按 v1 先例显式 close 1008 并把业务码放进 reason。
        if _is_key_authenticated_call():
            try:
                await assert_open_api_app_visible(workflow_id, login_user)
            except OpenApiNotFoundError:
                await websocket.close(
                    code=http_status.WS_1008_POLICY_VIOLATION, reason=str(OpenApiNotFoundError.Code)
                )
                return
        await chat_manager.dispatch_client(
            websocket,
            workflow_id,
            chat_id,
            login_user,
            WorkType.WORKFLOW,
            websocket,
            input_guard=build_input_guard(login_user, workflow_id),
            output_guard=build_output_guard(login_user, workflow_id),
        )
    except WebSocketException as exc:
        logger.error(f"Websocket exception: {exc!s}")
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR, reason=str(exc))
    except Exception as e:
        logger.error(f"Websocket handle error: {e!s}")
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR, reason=str(e))
