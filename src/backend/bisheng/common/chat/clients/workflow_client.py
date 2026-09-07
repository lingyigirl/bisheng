import asyncio
import json

from fastapi import Request, WebSocket
from loguru import logger

from bisheng.api.services.audit_log import AuditLogService
from bisheng.api.v1.schema.workflow import WorkflowEventType
from bisheng.common.chat.clients.base import BaseClient
from bisheng.common.chat.types import WorkType
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.chat import WorkflowOfflineError
from bisheng.database.models.flow import FlowDao, FlowStatus
from bisheng.database.models.message import ChatMessage, ChatMessageDao
from bisheng.utils import generate_uuid, get_request_ip
from bisheng.worker.workflow.redis_callback import RedisCallback
from bisheng.worker.workflow.tasks import continue_workflow, execute_workflow, workflow_stateful_worker
from bisheng.workflow.common.workflow import WorkflowStatus


class WorkflowClient(BaseClient):
    def __init__(
        self,
        request: Request,
        client_key: str,
        client_id: str,
        chat_id: str,
        user_id: int,
        login_user: UserPayload,
        work_type: WorkType,
        websocket: WebSocket,
        **kwargs,
    ):
        super().__init__(request, client_key, client_id, chat_id, user_id, login_user, work_type, websocket, **kwargs)

        self.workflow: RedisCallback | None = None
        self.latest_history: ChatMessage | None = None
        self.hash_key = None
        self.ws_closed = False
        self.run_lock = asyncio.Lock()
        # F056: once the output policy blocks, remaining stream frames are dropped
        # until the next run resets the guard.
        self._output_blocked = False

    async def close(self, force_stop=False):
        # If the user is not actively stopping, setwsTurn the flag off, but there is no need to abortworkflowExecution
        if not force_stop:
            self.ws_closed = True
        # Non-Session Mode OffworkflowImplementation, Session mode determines if the user took the initiative to close
        if self.workflow:
            if force_stop or not self.chat_id:
                await self.workflow.async_set_workflow_stop()
                workflow_over = await self.workflow_run()
                while not workflow_over:
                    if self.ws_closed:
                        break
                    workflow_over = await self.workflow_run()
                    await asyncio.sleep(0.5)
        else:
            await self.send_response("processing", "close", "")
        await super().close()

    async def _handle_message(self, message: dict[any, any]):
        logger.debug("----------------------------- start handle message -----------------------")
        if message.get("action") == "init_data":
            # InisialisasiworkflowDATA
            await self.init_workflow(message)
        elif message.get("action") == "check_status":
            await self.check_status(message)
        elif message.get("action") == "input":
            await self.handle_user_input(message.get("data"))
        elif message.get("action") == "stop":
            await self.close(force_stop=True)
            # await self.stop_handle_message(message)
        else:
            logger.warning("not support action: %s", message.get("action"))

    async def init_history(self):
        if not self.chat_id:
            return
        self.latest_history = await ChatMessageDao.aget_latest_message_by_chatid(self.chat_id)
        if not self.latest_history:
            # The user clicks New Session to log the audit
            AuditLogService.create_chat_workflow(self.login_user, get_request_ip(self.request), self.client_id)

    async def check_status(self, message: dict, is_init: bool = False) -> (bool, str):
        """
        bool: Indicates if re-execution is requiredworkflow
        """
        # chat ws connection first handle
        workflow_id = message.get("flow_id", self.client_id)
        self.chat_id = message.get("chat_id", "")
        unique_id = generate_uuid()
        if self.chat_id:
            await self.init_history()
            unique_id = f"{self.chat_id}_async_task_id"
        logger.debug(f"init workflow with unique_id: {unique_id}, workflow_id: {workflow_id}, chat_id: {self.chat_id}")
        self.workflow = RedisCallback(unique_id, workflow_id, self.chat_id, self.user_id)
        # JudgingworkflowWhether it is online, if it is not online, close the currentwebsocketLinks
        workflow_db = FlowDao.get_flow_by_id(workflow_id)
        if workflow_db.status != FlowStatus.ONLINE.value and self.chat_id:
            self.workflow.set_workflow_stop()
            try:
                await WorkflowOfflineError().websocket_close_message(websocket=self.websocket, close_ws=False)
                await self.send_response("processing", "close", "")
            except:
                logger.warning("websocket is closed")
                pass
            self.workflow.clear_workflow_status()
            self.workflow = None
            logger.debug("workflow is offline not support with chat")
            return False, unique_id

        status_info = self.workflow.get_workflow_status()
        if not status_info:
            # Indicates that the last run was completed
            self.workflow = None
            if self.latest_history and not is_init:
                # Let the front-end terminate the last run
                await self.send_response("processing", "close", "")
            return True, unique_id
        # Indicates that the session is still running
        if status_info["status"] == WorkflowStatus.INPUT.value and self.latest_history:
            # If it is a state waiting for user input, you need to resend the last input message to the front-end
            if self.latest_history.category in [
                WorkflowEventType.UserInput.value,
                WorkflowEventType.OutputWithInput.value,
                WorkflowEventType.OutputWithChoose.value,
            ]:
                send_message = self.latest_history.model_dump()
                send_message["message"] = json.loads(send_message["message"])
                send_message["message_id"] = send_message.pop("id")
                await self.send_json(send_message)

        await self.send_response("processing", "begin", "")
        logger.debug("init workflow over, continue run workflow")
        await self._reset_output_guard()
        await self.workflow_run()
        return False, unique_id

    async def get_execute_worker(self) -> str | None:
        if not self.hash_key:
            self.hash_key = self.chat_id if self.chat_id else generate_uuid()
        return await workflow_stateful_worker.find_task_node(self.hash_key)

    async def init_workflow(self, message: dict):
        if self.workflow is not None:
            return
        try:
            workflow_data = message.get("data")
            workflow_id = message.get("flow_id", self.client_id)
            flag, unique_id = await self.check_status(message, is_init=True)
            # Description workflow In operation or offline
            if not flag:
                return
            # Start a new workflow
            self.workflow = RedisCallback(unique_id, workflow_id, self.chat_id, self.user_id)
            await self.workflow.async_set_workflow_data(workflow_data)
            await self.workflow.async_set_workflow_status(WorkflowStatus.WAITING.value)
            # Start asynchronous task

            execute_workflow.apply_async(
                [unique_id, workflow_id, self.chat_id, self.user_id], queue=await self.get_execute_worker()
            )
            await self.send_response("processing", "begin", "")
            await self._reset_output_guard()
            await self.workflow_run()
        except Exception as e:
            logger.exception("init_workflow_error")
            self.workflow = None
            await self.send_response("error", "over", {"status_code": 500, "message": str(e)})
            return

    async def workflow_run(self):
        async with self.run_lock:
            return await self._workflow_run()

    async def _reset_output_guard(self):
        """Start a fresh guard round before each new run."""
        self._output_blocked = False
        if self.output_guard is not None and hasattr(self.output_guard, 'reset'):
            await self.output_guard.reset()

    async def _guard_output_event(self, event):
        """Guard streamed workflow answer events (F056, AC-09..14).

        ``RedisCallback.get_response_until_break`` yields ``ChatResponse`` models:
        stream chunks are type='stream' / category='stream_msg' with the text in
        ``message['msg']``; the stream-over frame (type='end') carries the final
        full answer and is rewritten with the guarded accumulated text (design 坑 6).
        Returns None to drop a frame. Duck-typed on purpose: importing ChatResponse
        here would add a common→api import (RULE-1), so only the attributes this
        guard needs are read.
        """
        guard = self.output_guard
        if guard is None:
            return event
        event_type = getattr(event, 'type', None)
        if event_type not in ('stream', 'end', 'end_cover'):
            return event
        if getattr(event, 'category', None) != WorkflowEventType.StreamMsg.value:
            return event
        message = getattr(event, 'message', None)
        if not isinstance(message, dict) or not isinstance(message.get('msg'), str):
            return event
        if event_type == 'stream':
            if self._output_blocked:
                return None
            result = await guard(self.chat_id, message['msg'])
            if result.blocked:
                self._output_blocked = True
            new_message = {**message, 'msg': result.safe_text}
        else:
            await guard(self.chat_id, '')  # flush the mask-mode hold-back tail
            new_message = {**message, 'msg': await guard.full_text()}
        if hasattr(event, 'model_copy'):
            return event.model_copy(update={'message': new_message})
        if isinstance(event, dict):
            return {**event, 'message': new_message}
        return event

    async def _workflow_run(self):
        logger.debug("start workflow run")
        if not self.workflow:
            logger.warning("workflow is over by other task")
            return True

        # Needs to constantly evolve fromredisGet inworkflowReturned Message
        async for event in self.workflow.get_response_until_break():
            guarded = await self._guard_output_event(event)
            if guarded is None:
                continue
            await self.send_json(guarded)

        status_info = await self.workflow.async_get_workflow_status()
        if not status_info or status_info["status"] in [WorkflowStatus.FAILED.value, WorkflowStatus.SUCCESS.value]:
            await self.send_response("processing", "close", "")
            logger.debug(f"workflow is {status_info}, clear workflow object")
            await self.workflow.async_clear_workflow_status()
            self.workflow = None
            return True

        # Description runs to the state to be entered
        elif status_info["status"] != WorkflowStatus.INPUT.value:
            logger.warning(f"workflow status is unknown: {status_info}")
        return False

    async def handle_user_input(self, data: dict):
        logger.info(f"get user input: {data}")
        if not self.workflow:
            logger.warning("workflow is over")
            return
        status_info = await self.workflow.async_get_workflow_status()
        if status_info["status"] != WorkflowStatus.INPUT.value:
            logger.warning(f"workflow is not input status: {status_info}")
        else:
            user_input = {}
            message_id = None
            new_message = None
            new_files = None
            # Currently one input node is supported
            for node_id, node_info in data.items():
                user_input[node_id] = node_info["data"]
                message_id = node_info.get("message_id")
                new_message = node_info.get("message")
                # Attachments as data — the node reads them from
                # ``dialog_files_content``, this is what the message keeps.
                new_files = node_info.get("files")
                break
            await self.workflow.async_set_user_input(
                user_input, message_id=message_id, message_content=new_message, files=new_files
            )
            await self.workflow.async_set_workflow_status(WorkflowStatus.INPUT_OVER.value)
            continue_workflow.apply_async(
                [self.workflow.unique_id, self.workflow.workflow_id, self.workflow.chat_id, self.workflow.user_id],
                queue=await self.get_execute_worker(),
            )
            await self.workflow_run()
        # await self.workflow_run()
