import sys
import time

from loguru import logger

from bisheng.api.services.workflow import WorkFlowService
from bisheng.chat_session.domain.services.chat_turn_service import ChatTurnService
from bisheng.common.chat.utils import get_input_snapshot
from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum, BaseTelemetryTypeEnum, StatusEnum
from bisheng.common.schemas.telemetry.event_data_schema import ApplicationProcessEventData
from bisheng.common.services import telemetry_service
from bisheng.common.services.config_service import settings
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.core.logger import trace_id_var
from bisheng.database.models.flow import FlowDao
from bisheng.utils.exceptions import IgnoreException
from bisheng.worker.main import bisheng_celery
from bisheng.worker.utils.stateful_worker import StatefulWorker
from bisheng.worker.workflow.follow_up_history import load_history
from bisheng.worker.workflow.redis_callback import RedisCallback
from bisheng.workflow.common.node import NodeType
from bisheng.workflow.common.workflow import WorkflowStatus
from bisheng.workflow.graph.workflow import Workflow

# workflow execute stateful worker for assigning tasks to bound workers
workflow_stateful_worker = StatefulWorker(queue_prefix="workflow_celery", bound_nodes_prefix="workflow_bound_nodes:")
# Stores global workflow objects
_global_workflow: dict[str, Workflow] = {}


def _clear_workflow_obj(unique_id: str):
    """Clear Global Workflow Objects"""
    if unique_id in _global_workflow:
        del _global_workflow[unique_id]
        logger.debug(f"clear workflow object for unique_id: {unique_id}")
    else:
        logger.warning(f"workflow object not found for unique_id: {unique_id}")


def _judge_workflow_status(redis_callback: RedisCallback, workflow: Workflow):
    status = workflow.status()
    reason = workflow.reason()
    if workflow.status() in [WorkflowStatus.SUCCESS.value, WorkflowStatus.FAILED.value]:
        redis_callback.set_workflow_status(status, reason)
        _clear_workflow_obj(redis_callback.unique_id)
        return
    if workflow.status() == WorkflowStatus.INPUT.value:
        # If it is an input state, place the object in memory
        _global_workflow[redis_callback.unique_id] = workflow
        # redis_callback.save_workflow_object(workflow)
        redis_callback.set_workflow_status(status, reason)
        return
    logger.error(f"unexpected workflow status error: {status}")
    redis_callback.set_workflow_status(WorkflowStatus.FAILED.value, f"workflow run failed, unexpected status: {status}")
    _clear_workflow_obj(redis_callback.unique_id)


def _form_input_node(workflow_data: dict) -> dict | None:
    """F067: The flow's form-mode input node, or None -- which also answers "is this a
    form-mode flow at all". Node fields live under ``node["data"]`` (see
    ``GraphEngine.init_nodes``), and the mode is the input node's ``tab.value``.
    """
    for node in (workflow_data or {}).get("nodes", []):
        node_data = node.get("data") or {}
        if node_data.get("type") == NodeType.INPUT.value and (node_data.get("tab") or {}).get("value") == "form_input":
            return node_data
    return None


def _dialog_input_node(workflow_data: dict) -> dict | None:
    """返回对话模式的输入节点；不是对话模式时返回 None。"""
    for node in (workflow_data or {}).get("nodes", []):
        node_data = node.get("data") or {}
        if (
            node_data.get("type") == NodeType.INPUT.value
            and (node_data.get("tab") or {}).get("value") == "dialog_input"
        ):
            return node_data
    return None


def _follow_up_round(workflow_data: dict, redis_callback: RedisCallback, user_input: str | None):
    """F067: ``(input node id, the first round's outputs)`` when this round is a
    follow-up round, else None -- "else" meaning the round runs exactly as before.

    Three conditions, each guarding a distinct bad outcome (AC-02 / AC-13):
    a question arrived at all, the flow's input node is form mode (a dialog input node
    runs every round and has no first round to reuse), and the session has the first
    round's outputs on record (without them there is nothing to reuse, and letting the
    form branch run with no submission feeds downstream nodes empty fields).
    """
    if not isinstance(user_input, str) or not user_input.strip():
        # 契约是字符串（帧契约见 design §4.2）。**必须显式判类型**：真值判断放行
        # `["敏感词"]` 这类载荷 —— `_guard_input_texts` 只检测字符串，于是它绕过输入护栏
        # 进图、落库，再被 `str()` 成 "[敏感词]"；dict 更糟，`save_context` 的 pydantic 校验
        # 会抛进 `_execute_workflow` 的宽 except，整轮 FAILED。非字符串一律按首轮处理。
        return None
    node_data = _form_input_node(workflow_data)
    if node_data is None:
        return None
    outputs = redis_callback.read_input_outputs()
    if not outputs:
        logger.warning(
            "follow-up question on chat={} has no recorded first-round inputs, treating it as a first round",
            redis_callback.chat_id,
        )
        return None
    return node_data["id"], outputs


def _start_follow_up_round(
    redis_callback: RedisCallback,
    workflow: Workflow,
    input_node_id: str,
    outputs: dict,
    user_input: str,
):
    """F067: 把追问轮要带的东西灌进这一轮的图（design 决策 4 / 9）。

    **必须在 `Workflow(...)` 构造之后调用** —— `StartNode.__init__` 会在构造期间覆盖
    `history_memory`，构造前注入会被静默冲掉（坑 2）。

    三件事：首轮产出 + 本轮问题预置进变量池（键 `{输入节点 id}.{键}`，与节点的产出键
    同名同形，节点因此认出「产出已在池子里」而早退）；前序问答灌进 `history_memory`；
    本轮问题显式落库（追问轮不产生 `UserInput` 事件，不写就丢 —— 决策 5）。
    """
    graph_state = workflow.graph_engine.graph_state
    for key, value in outputs.items():
        graph_state.set_variable(input_node_id, key, value)
    graph_state.set_variable(input_node_id, "user_input", user_input)

    memory = graph_state.history_memory
    # 窗口沿用节点参数：`StartNode` 已把配置好的轮数写进 `memory.k`（决策 4，不新增配置）。
    # memory 缺席（流程没有 start 节点）时窗口为 0 → 读不到历史，也不会去 save_context。
    window = memory.k if memory is not None else 0
    for content, sender in load_history(redis_callback.chat_id, redis_callback.user_id, window):
        graph_state.save_context(content=content, msg_sender=sender)

    redis_callback.save_follow_up_question(user_input)


def _start_dialog_round(workflow: Workflow, redis_callback: RedisCallback):
    """把该会话已有的问答回灌进对话模式工作流。

    必须在 ``Workflow(...)`` 构造之后调用，避免 ``StartNode`` 重新初始化
    ``history_memory`` 时把刚灌入的内容覆盖掉。

    这里只恢复用户问题与最终回答；节点变量、工具轨迹和检索结果不属于本轮
    短期记忆范围，后续如需复用应单独设计。历史窗口沿用输入节点的
    ``chat_history`` 配置，不新增用户可见配置。
    """
    graph_state = workflow.graph_engine.graph_state
    memory = graph_state.history_memory
    if memory is None:
        return

    turn = ChatTurnService.start_turn(
        turn_id=redis_callback.run_id,
        chat_id=redis_callback.chat_id,
        flow_id=redis_callback.workflow_id,
        user_id=redis_callback.user_id,
        tenant_id=get_current_tenant_id() or redis_callback.tenant_id,
    )
    redis_callback.business_turn_id = turn.turn_id
    redis_callback.business_turn_no = turn.turn_no

    history_context = ChatTurnService.load_history_context(
        chat_id=redis_callback.chat_id,
        user_id=redis_callback.user_id,
        tenant_id=turn.tenant_id,
        before_turn_no=turn.turn_no,
        raw_window=memory.k,
    )
    if history_context.turns or history_context.summaries:
        graph_state.set_history_context(
            turns=history_context.turns,
            summaries=history_context.summaries,
        )
    else:
        # 旧会话尚未回填 chat_turn 时继续兼容原消息表读取。
        history = load_history(redis_callback.chat_id, redis_callback.user_id, memory.k)
        graph_state.set_completed_history(history)
    graph_state.begin_current_turn()
    logger.debug(
        "hydrated dialog workflow history chat_id={} turn_id={} turns={} summaries={} window={}",
        redis_callback.chat_id,
        turn.turn_id,
        len(history_context.turns),
        len(history_context.summaries),
        memory.k,
    )


def _enqueue_turn_summary(turn_id: str) -> None:
    """异步生成当前轮 D0 摘要。"""
    from bisheng.worker.workflow.chat_summary_tasks import summarize_chat_turn

    summarize_chat_turn.delay(turn_id)


def _finalize_dialog_turn(workflow: Workflow, redis_callback: RedisCallback) -> None:
    """按工作流最终状态提交业务轮次。"""
    turn_id = getattr(redis_callback, "business_turn_id", None)
    if not turn_id:
        return

    status = workflow.status()
    if status == WorkflowStatus.SUCCESS.value:
        answer_message_id = getattr(redis_callback, "last_final_message_id", None)
        answer_text = getattr(redis_callback, "last_final_answer_text", None)
        if answer_message_id is None or not answer_text:
            ChatTurnService.fail_turn(turn_id=turn_id, error_code="EMPTY_FINAL_ANSWER")
            return
        ChatTurnService.record_answer(
            turn_id=turn_id,
            answer_message_id=answer_message_id,
            answer_text=answer_text,
        )
        _enqueue_turn_summary(turn_id)
        return

    if status == WorkflowStatus.FAILED.value:
        ChatTurnService.fail_turn(
            turn_id=turn_id,
            error_code=workflow.reason() or "WORKFLOW_FAILED",
        )


def _execute_workflow(
    unique_id: str, workflow_id: str, chat_id: str, user_id: int, source: str = "platform", user_input: str = None
):
    # F022 INV-T18: resolve Flow.tenant_id once; reuse for both Workflow
    # node tenant threading and RedisCallback's sync workbench reads.
    workflow_info = FlowDao.get_flow_by_id(workflow_id)
    flow_tenant_id = getattr(workflow_info, "tenant_id", None)
    # F041: Flow creator (config author) — nodes filter knowledge-space retrieval
    # by this identity when the permission toggle is OFF (distinct from `user_id`,
    # the runtime user who triggered the run).
    flow_user_id = getattr(workflow_info, "user_id", None)
    redis_callback = RedisCallback(unique_id, workflow_id, chat_id, user_id, source=source, tenant_id=flow_tenant_id)
    try:
        # F052 fix: one round, one id -- minted here (not in the celery task) so the
        # round's identity is established wherever a round starts, and so callers that
        # invoke this helper directly keep working unchanged. It is also what makes
        # `trace_id_var` below round-scoped, which is what the monitor keys on.
        trace_id_var.set(redis_callback.mint_run_id())
        # F052: announce the round while it runs. `input_snapshot` is deliberately left
        # empty -- the round's own question row is written later, by the input node, so
        # reading it here would publish the PREVIOUS round's text. The event the task's
        # `finally` writes is newer on the same trace, so `collapse` keeps that one and
        # the snapshot appears once the round is done.
        round_start = time.time()
        _report_run_event(
            workflow_id=workflow_id,
            chat_id=chat_id,
            user_id=user_id,
            app_name=workflow_info.name if workflow_info else None,
            start_time=round_start,
            end_time=round_start,
            status=StatusEnum.RUNNING,
        )
        # update workflow status
        redis_callback.set_workflow_status(WorkflowStatus.RUNNING.value)
        # get workflow data
        workflow_data = redis_callback.get_workflow_data()
        if not workflow_data:
            raise Exception("workflow data not found maybe data is expired")

        # init workflow
        workflow_conf = settings.get_workflow_conf()
        workflow_name = workflow_info.name if workflow_info else workflow_id
        # F067: a round carrying a follow-up question reuses the first round's form
        # inputs -- it must not stop at the input node asking for the form again.
        # `_follow_up_round` returns None for every other round, which is when nothing
        # here changes at all.
        follow_up = _follow_up_round(workflow_data, redis_callback, user_input)
        workflow = Workflow(
            workflow_id,
            workflow_name,
            user_id,
            workflow_data,
            False,
            workflow_conf.max_steps,
            workflow_conf.timeout,
            redis_callback,
            tenant_id=flow_tenant_id,
            flow_user_id=flow_user_id,
            skip_input_interrupt=follow_up is not None,
        )
        redis_callback.workflow = workflow
        if follow_up:
            _start_follow_up_round(redis_callback, workflow, *follow_up, user_input=user_input)
        elif _dialog_input_node(workflow_data) is not None:
            _start_dialog_round(workflow, redis_callback)
        workflow.run()
        _finalize_dialog_turn(workflow, redis_callback)
        _judge_workflow_status(redis_callback, workflow)
    except IgnoreException as e:
        logger.warning(f"execute_workflow ignore error: {e}")
        if getattr(redis_callback, "business_turn_id", None):
            ChatTurnService.fail_turn(turn_id=redis_callback.business_turn_id, error_code="IGNORED")
        redis_callback.set_workflow_status(WorkflowStatus.FAILED.value, str(e))
        _clear_workflow_obj(redis_callback.unique_id)
    except Exception as e:
        logger.exception("execute_workflow error")
        if getattr(redis_callback, "business_turn_id", None):
            ChatTurnService.fail_turn(turn_id=redis_callback.business_turn_id, error_code="WORKFLOW_ERROR")
        redis_callback.set_workflow_status(WorkflowStatus.FAILED.value, str(e)[:100])
        _clear_workflow_obj(redis_callback.unique_id)


def _report_run_event(
    *,
    workflow_id: str,
    chat_id: str,
    user_id: int,
    app_name: str | None,
    start_time: float,
    end_time: float,
    status: StatusEnum,
    input_snapshot: str | None = None,
) -> None:
    """One ``application_process`` event for a workflow round.

    A round writes two of these -- RUNNING when it starts, its real outcome when it ends.
    Both run under the same run id, so the monitor's ``collapse`` on ``trace_id`` keeps
    the finished one and the running row is updated in place: one round stays one
    transaction (AC-09) and is still visible while it runs (AC-11).
    """
    try:
        telemetry_service.log_event_sync(
            user_id=user_id,
            event_type=BaseTelemetryTypeEnum.APPLICATION_PROCESS,
            trace_id=trace_id_var.get(),
            event_data=ApplicationProcessEventData(
                app_id=workflow_id,
                app_name=app_name or workflow_id,
                app_type=ApplicationTypeEnum.WORKFLOW,
                chat_id=chat_id,
                start_time=int(start_time),
                end_time=int(end_time),
                process_time=int((end_time - start_time) * 1000),
                status=status,
                input_snapshot=input_snapshot,
            ),
        )
    except Exception:
        # Observability must not take the round down with it -- the RUNNING event is
        # written before any of the round's work has happened, so a telemetry outage
        # would otherwise fail the run it was only trying to describe. Same shape as
        # the model-invoke telemetry in llm/domain/utils.py.
        logger.exception("upload application process telemetry failed")


@bisheng_celery.task
def execute_workflow(
    unique_id: str, workflow_id: str, chat_id: str, user_id: int, source: str = "platform", user_input: str = None
):
    """Implementationworkflow"""
    trace_id_var.set(unique_id)
    start_time = time.time()
    try:
        _execute_workflow(unique_id, workflow_id, chat_id, user_id, source, user_input)
    finally:
        end_time = time.time()
        workflow_info = WorkFlowService.get_one_workflow_simple_info_sync(workflow_id)
        # F046: an exception propagating through the finally marks the run failed
        run_status = StatusEnum.FAILED if sys.exc_info()[0] is not None else StatusEnum.SUCCESS
        _report_run_event(
            workflow_id=workflow_id,
            chat_id=chat_id,
            user_id=user_id,
            app_name=workflow_info.name if workflow_info else None,
            start_time=start_time,
            end_time=end_time,
            status=run_status,
            input_snapshot=get_input_snapshot(chat_id),
        )


def _continue_workflow(unique_id: str, workflow_id: str, chat_id: str, user_id: int, source: str = "platform"):
    """Resumeworkflow"""
    redis_callback = RedisCallback(unique_id, workflow_id, chat_id, user_id, source=source)
    try:
        # F052 fix: resume under the id the round was minted with, so this half of a
        # form round lands under the same transaction as its `execute_workflow` half
        # (AC-09). Falls back to the chat-scoped id when nothing was minted, which at
        # worst re-merges the rounds the way they behaved before the split. MUST run
        # after the task's own `trace_id_var.set(unique_id)`, which it overrides.
        trace_id_var.set(redis_callback.run_id)
        existing_turn = ChatTurnService.get_turn(redis_callback.run_id)
        if existing_turn is not None:
            redis_callback.business_turn_id = existing_turn.turn_id
            redis_callback.business_turn_no = existing_turn.turn_no
        workflow = _global_workflow.get(redis_callback.unique_id, None)
        if not workflow:
            raise Exception("workflow object not found maybe data is expired")
        # 继续执行时会新建 RedisCallback，但工作流仍持有上一阶段创建的回调。
        # 答案落库和最终提交必须使用同一个回调实例，否则 last_final_message_id
        # 会留在图对象里，而 _finalize_dialog_turn 读到的是新实例的空值。
        workflow_callback = getattr(workflow.graph_engine, "callback", None)
        if existing_turn is not None:
            for callback in (redis_callback, workflow_callback):
                if callback is not None:
                    callback.business_turn_id = existing_turn.turn_id
                    callback.business_turn_no = existing_turn.turn_no
        if workflow.status() not in [WorkflowStatus.INPUT.value, WorkflowStatus.INPUT_OVER.value]:
            raise Exception(f"workflow status is {workflow.status()} not INPUT")
        user_input = redis_callback.get_user_input()
        if not user_input:
            raise IgnoreException("workflow continue not found user input")
        redis_callback.set_workflow_status(WorkflowStatus.RUNNING.value)
        workflow.run(user_input)
        _finalize_dialog_turn(workflow, workflow_callback or redis_callback)
        _judge_workflow_status(redis_callback, workflow)
    except IgnoreException as e:
        logger.warning(f"continue_workflow ignore error: {e}")
        if getattr(redis_callback, "business_turn_id", None):
            ChatTurnService.fail_turn(turn_id=redis_callback.business_turn_id, error_code="IGNORED")
        redis_callback.set_workflow_status(WorkflowStatus.FAILED.value, str(e))
        _clear_workflow_obj(redis_callback.unique_id)
    except Exception as e:
        logger.exception("continue_workflow error")
        if getattr(redis_callback, "business_turn_id", None):
            ChatTurnService.fail_turn(turn_id=redis_callback.business_turn_id, error_code="WORKFLOW_ERROR")
        redis_callback.set_workflow_status(WorkflowStatus.FAILED.value, str(e)[:100])
        _clear_workflow_obj(redis_callback.unique_id)


@bisheng_celery.task
def continue_workflow(unique_id: str, workflow_id: str, chat_id: str, user_id: int, source: str = "platform"):
    """Resumeworkflow"""
    trace_id_var.set(unique_id)
    start_time = time.time()
    try:
        _continue_workflow(unique_id, workflow_id, chat_id, user_id, source)
    finally:
        end_time = time.time()
        workflow_info = WorkFlowService.get_one_workflow_simple_info_sync(workflow_id)
        # F046: an exception propagating through the finally marks the run failed
        run_status = StatusEnum.FAILED if sys.exc_info()[0] is not None else StatusEnum.SUCCESS
        telemetry_service.log_event_sync(
            user_id=user_id,
            event_type=BaseTelemetryTypeEnum.APPLICATION_PROCESS,
            trace_id=trace_id_var.get(),
            event_data=ApplicationProcessEventData(
                app_id=workflow_id,
                app_name=workflow_info.name if workflow_info else workflow_id,
                app_type=ApplicationTypeEnum.WORKFLOW,
                chat_id=chat_id,
                start_time=int(start_time),
                end_time=int(end_time),
                process_time=int((end_time - start_time) * 1000),
                status=run_status,
                input_snapshot=get_input_snapshot(chat_id),
            ),
        )


@bisheng_celery.task
def stop_workflow(unique_id: str, workflow_id: str, chat_id: str, user_id: int):
    """Stopworkflow"""
    trace_id_var.set(unique_id)

    redis_callback = RedisCallback(unique_id, workflow_id, chat_id, user_id)
    if unique_id not in _global_workflow:
        turn = ChatTurnService.get_turn(redis_callback.run_id)
        if turn is not None:
            ChatTurnService.cancel_turn(turn_id=turn.turn_id)
        redis_callback.set_workflow_status(WorkflowStatus.FAILED.value, "workflow stop by user")
        logger.warning("stop_workflow called but workflow not found in global cache")
        return
    workflow = _global_workflow[unique_id]
    workflow.stop()

    while workflow.status() == WorkflowStatus.RUNNING.value:
        time.sleep(0.3)
    status, reason = workflow.status(), workflow.reason()
    if status != WorkflowStatus.FAILED.value:
        status = WorkflowStatus.FAILED.value
        reason = "workflow stop by user"
        redis_callback.set_workflow_status(status, reason)
    turn = ChatTurnService.get_turn(redis_callback.run_id)
    if turn is not None:
        ChatTurnService.cancel_turn(turn_id=turn.turn_id)
    _clear_workflow_obj(unique_id)
    logger.info(f"workflow stop by user {user_id}")
