"""Native projection adapter: delegate to the platform's existing run chains.

Assistant projections run ``AssistantAgent`` in-process with ``chat_id=""`` (the
same pattern as F077's assistant runner). Workflow projections reuse the F054
programmatic run sequence (prewrite data → execute_workflow → read events),
with one INPUT submission; a second INPUT or a timeout is reported as 12106 for
the synchronous invoke face (scheduled workflows keep F077's full INPUT loop).
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid

from loguru import logger

from bisheng.common.errcode.managed_agent import (
    ManagedAgentConfigInvalidError,
    ManagedAgentUpstreamError,
)
from bisheng.managed_agent.domain.services.adapters.base import (
    AgentResult,
    CallerIdentity,
    HealthResult,
)


def _query(payload) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        if "query" in payload:
            return str(payload["query"])
        if isinstance(payload.get("messages"), list) and payload["messages"]:
            last = payload["messages"][-1]
            if isinstance(last, dict):
                return str(last.get("content", ""))
    return str(payload)


class NativeAdapter:
    async def invoke(
        self,
        agent,
        payload,
        session_id: str | None,
        caller: CallerIdentity,
    ) -> AgentResult:
        if agent.native_app_type == 5:
            return await self._invoke_assistant(agent, payload, caller)
        if agent.native_app_type == 10:
            return await asyncio.to_thread(self._invoke_workflow, agent, payload, caller)
        raise ManagedAgentConfigInvalidError(msg="native projection has an unsupported app type")

    async def _invoke_assistant(self, agent, payload, caller: CallerIdentity) -> AgentResult:
        from bisheng.api.services.assistant_agent import AssistantAgent
        from bisheng.database.models.assistant import AssistantDao

        assistant = await AssistantDao.aget_one_assistant(str(agent.native_app_id))
        if assistant is None:
            raise ManagedAgentUpstreamError(msg="native assistant no longer exists")
        run_id = uuid.uuid4().hex
        try:
            runner = AssistantAgent(
                assistant_info=assistant,
                chat_id="",
                invoke_user_id=caller.user_id,
            )
            await runner.init_assistant()
            messages = await runner.run(_query(payload))
        except Exception as exc:
            logger.exception("managed_agent: native assistant invoke failed for {}", agent.native_app_id)
            raise ManagedAgentUpstreamError(msg=str(exc)[:200]) from exc
        output = ""
        if messages:
            last = messages[-1]
            output = getattr(last, "content", None) or str(last)
        return AgentResult(run_id=run_id, output=output)

    def _invoke_workflow(self, agent, payload, caller: CallerIdentity) -> AgentResult:
        from bisheng.database.models.flow import FlowDao, FlowStatus, FlowType
        from bisheng.utils import generate_uuid
        from bisheng.worker.workflow.redis_callback import RedisCallback
        from bisheng.worker.workflow.tasks import (
            continue_workflow,
            execute_workflow,
            workflow_stateful_worker,
        )
        from bisheng.workflow.common.workflow import WorkflowStatus

        flow = FlowDao.get_flow_by_id(str(agent.native_app_id))
        if flow is None or flow.flow_type != FlowType.WORKFLOW.value:
            raise ManagedAgentUpstreamError(msg="native workflow no longer exists")
        if flow.status != FlowStatus.ONLINE.value:
            raise ManagedAgentUpstreamError(msg="native workflow is offline")

        unique_id = generate_uuid()
        chat_id = ""
        workflow = RedisCallback(unique_id, str(flow.id), chat_id, caller.user_id)
        workflow.set_workflow_data(copy.deepcopy(flow.data))
        workflow.set_workflow_status(WorkflowStatus.WAITING.value)
        hash_key = generate_uuid()
        worker_node = workflow_stateful_worker.find_task_node_sync(hash_key)
        execute_workflow.apply_async([unique_id, str(flow.id), chat_id, caller.user_id], queue=worker_node)

        events = []
        try:
            while True:
                for event in workflow.sync_get_response_until_break():
                    events.append(event)
                status_info = workflow.get_workflow_status() or {}
                status = status_info.get("status")
                if status == WorkflowStatus.FAILED.value:
                    raise ManagedAgentUpstreamError(msg=status_info.get("reason", "workflow failed")[:200])
                if status == WorkflowStatus.INPUT.value:
                    if not events:
                        raise ManagedAgentUpstreamError(msg="workflow requested input without an event")
                    event = events[-1]
                    node_id = (event.message or {}).get("node_id")
                    if not node_id:
                        raise ManagedAgentUpstreamError(msg="workflow input has no node id")
                    if isinstance(payload, dict):
                        user_input = payload
                    else:
                        user_input = {"user_input": _query(payload)}
                    workflow.set_user_input({node_id: user_input})
                    workflow.set_workflow_status(WorkflowStatus.INPUT_OVER.value)
                    worker_node = workflow_stateful_worker.find_task_node_sync(hash_key)
                    continue_workflow.apply_async([unique_id, str(flow.id), chat_id, caller.user_id], queue=worker_node)
                    events = []
                    continue
                if status == WorkflowStatus.SUCCESS.value:
                    break
                raise ManagedAgentUpstreamError(msg=f"workflow status is unknown: {status}")
        except Exception:
            try:
                workflow.set_workflow_stop()
            except Exception:
                logger.warning("managed_agent: failed to stop workflow {}", unique_id)
            raise

        answer = None
        from bisheng.api.v1.schema.workflow import WorkflowEventType

        for event in events:
            if event.category in [
                WorkflowEventType.OutputMsg.value,
                WorkflowEventType.OutputWithInput.value,
                WorkflowEventType.OutputWithChoose.value,
            ]:
                answer = (event.message or {}).get("msg", "")
                break
            if event.category == WorkflowEventType.StreamMsg.value and event.type != "stream":
                answer = (event.message or {}).get("msg", "")
                break
        if answer is None:
            raise ManagedAgentUpstreamError(msg="workflow produced no output")
        return AgentResult(run_id=unique_id, output=answer)

    async def health_check(self, agent) -> HealthResult:
        started = time.monotonic()
        if agent.native_app_type == 5:
            from bisheng.database.models.assistant import AssistantDao, AssistantStatus

            row = await AssistantDao.aget_one_assistant(str(agent.native_app_id))
            ok = row is not None and int(getattr(row, "is_delete", 0) or 0) == 0
            if ok:
                ok = row.status == AssistantStatus.ONLINE.value
            return HealthResult(ok=ok, latency_ms=int((time.monotonic() - started) * 1000), detail="native assistant")
        if agent.native_app_type == 10:
            from bisheng.database.models.flow import FlowDao, FlowStatus, FlowType

            row = await FlowDao.aget_flow_by_id(str(agent.native_app_id))
            ok = row is not None and row.flow_type == FlowType.WORKFLOW.value
            if ok:
                ok = row.status == FlowStatus.ONLINE.value
            return HealthResult(ok=ok, latency_ms=int((time.monotonic() - started) * 1000), detail="native workflow")
        return HealthResult(ok=False, latency_ms=0, detail="unsupported native app type")
