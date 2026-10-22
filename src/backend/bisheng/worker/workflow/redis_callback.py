import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator

from langchain_core.documents import Document
from loguru import logger

from bisheng.api.v1.schema.workflow import WorkflowEventType
from bisheng.api.v1.schemas import ChatResponse
from bisheng.chat_session.domain.services.chat_turn_service import ChatTurnService
from bisheng.citation.domain.schemas.citation_schema import CitationRegistryItemSchema
from bisheng.citation.domain.services.citation_prompt_helper import (
    collect_rag_citation_registry_items,
    save_message_citations_sync,
    select_registry_items_for_persistence,
)
from bisheng.common.chat.utils import sync_judge_source, sync_process_source_document
from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum, BaseTelemetryTypeEnum
from bisheng.common.errcode.flow import (
    WorkFlowNodeRunMaxTimesError,
    WorkFlowNodeUpdateError,
    WorkFlowTaskBusyError,
    WorkFlowTaskOtherError,
    WorkFlowVersionUpdateError,
    WorkFlowWaitUserTimeoutError,
)
from bisheng.common.errcode.http_error import ServerError
from bisheng.common.schemas.telemetry.event_data_schema import NewMessageSessionEventData
from bisheng.common.services import telemetry_service
from bisheng.common.services.config_service import settings
from bisheng.common.utils.title_generator import generate_conversation_title_sync
from bisheng.core.cache.redis_manager import get_redis_client_sync
from bisheng.core.logger import trace_id_var
from bisheng.core.storage.chat_attachment import promote_chat_attachments_sync
from bisheng.database.models.flow import FlowDao, FlowType
from bisheng.database.models.message import ChatMessage, ChatMessageDao
from bisheng.database.models.session import MessageSession, MessageSessionDao
from bisheng.database.models.workflow_node_run import NodeRunDao
from bisheng.llm.domain import LLMService
from bisheng.sensitive_word.domain.services.answer_filter import filter_chat_response_message
from bisheng.utils.threadpool import thread_pool
from bisheng.workflow.callback.base_callback import BaseCallback
from bisheng.workflow.callback.event import (
    GuideQuestionData,
    GuideWordData,
    NodeEndData,
    NodeStartData,
    OutputMsgChooseData,
    OutputMsgData,
    OutputMsgInputData,
    StreamMsgData,
    StreamMsgOverData,
    UserInputData,
)
from bisheng.workflow.common.workflow import WorkflowStatus

#: F067: the namespace our payload occupies inside `chat_message.extra`. That column is
#: shared -- answer rows carry citation provenance in it -- so we neither replace it nor
#: write at its top level, and a future writer can tell whose keys are whose.
INPUT_OUTPUTS_EXTRA_KEY = "input_outputs"

#: The message rows that make up the conversation, as `follow_up_history` reads them back.
QUESTION_CATEGORY = "question"

#: How many question messages back the recorded inputs are looked for (F067). Each
#: follow-up round adds one row that does NOT carry them, so this bounds the number of
#: follow-up rounds in one session that can still find the first round's copy.
RECORDED_OUTPUTS_LOOKBACK = 30


class RedisCallback(BaseCallback):
    # Both are really set by __init__. Defaulted at class level because callbacks are
    # also built with ``__new__`` (the persistence tests, to avoid a live Redis): such an
    # object has no round-id key, and `run_id` then resolves to the chat-scoped
    # `unique_id` -- the same fallback an expired key gets.
    _run_id: str | None = None
    workflow_run_id_key: str | None = None

    def __init__(self, unique_id: str, workflow_id: str, chat_id: str, user_id: int, **kwargs):
        # F022 INV-T18: Owner tenant of the Flow (set by the celery task entry
        # via tasks.py from FlowDao.get_flow_by_id) — used by sync helpers
        # such as generate_session_title that resolve workbench config in a
        # worker context with no admin-scope ContextVar.
        self.tenant_id = kwargs.pop("tenant_id", None)
        super().__init__()
        # Unique for asynchronous tasksID
        self.unique_id = unique_id
        self.workflow_id = workflow_id
        self.chat_id = chat_id
        self.user_id = user_id
        self.workflow = None
        self.create_session = False
        self.source = kwargs.get("source", "platform")  # only platform or api

        self.new_session = None
        self.business_turn_id: str | None = None
        self.business_turn_no: int | None = None
        self.last_final_message_id: int | None = None
        self.last_final_answer_text: str | None = None
        # F067: the form-mode input node this round is waiting on, remembered when the
        # engine asks the user for it and used when that node finishes running.
        self._form_input_node_id: str | None = None

        self.redis_client = get_redis_client_sync()
        self.workflow_data_key = f"workflow:{unique_id}:data"
        self.workflow_status_key = f"workflow:{unique_id}:status"
        self.workflow_event_key = f"workflow:{unique_id}:event"
        self.workflow_input_key = f"workflow:{unique_id}:input"
        self.workflow_stop_key = f"workflow:{unique_id}:stop"
        # F052 fix: the id of the CURRENT round. `unique_id` has to stay chat-scoped
        # (check_status derives it from chat_id alone to re-attach a paused run), so a
        # round needs an identity of its own -- without one, every round of a chat
        # collapses into a single transaction and their node rows pile up in the detail.
        self.workflow_run_id_key = f"workflow:{unique_id}:run_id"
        self._run_id: str | None = None
        self.workflow_expire_time = settings.get_workflow_conf().timeout * 60 + 60

    def set_workflow_data(self, data: dict, override: dict = None):
        data = self.override_nodes_params(data, override)
        self.redis_client.set(self.workflow_data_key, data, expiration=self.workflow_expire_time)

    @staticmethod
    def override_nodes_params(data: dict, override: dict = None) -> dict:
        if not override:
            return data

        def replace_param(one_params: list[dict], one_node_id: str):
            for param in one_params:
                param_key = param.get("key")
                if param_key not in override[one_node_id]:
                    continue
                param["value"] = override[one_node_id][param_key]

        nodes = data.get("nodes", [])
        for node in nodes:
            node_data = node.get("data", {})
            node_id = node_data.get("id")
            if node_id not in override:
                continue
            group_params = node_data.get("group_params", [])
            for group_param in group_params:
                replace_param(group_param.get("params", []), node_id)
        return data

    async def async_set_workflow_data(self, data: dict):
        await self.redis_client.aset(self.workflow_data_key, data, expiration=self.workflow_expire_time)

    def get_workflow_data(self) -> dict:
        return self.redis_client.get(self.workflow_data_key)

    def _has_alive_worker(self) -> bool:
        """Whether a live worker is consuming the workflow queue.

        Imported lazily on purpose: ``bisheng.worker.workflow.tasks`` imports this
        module back, so a top-level import would be circular.
        """
        from bisheng.worker.workflow.tasks import workflow_stateful_worker

        return workflow_stateful_worker.has_alive_node_sync()

    async def _has_alive_worker_async(self) -> bool:
        from bisheng.worker.workflow.tasks import workflow_stateful_worker

        return await workflow_stateful_worker.has_alive_node()

    def set_workflow_status(self, status: str, reason: str = None):
        self.redis_client.set(
            self.workflow_status_key,
            {"status": status, "reason": reason, "time": time.time()},
            expiration=3600 * 24 * 7,
        )
        if status in [WorkflowStatus.FAILED.value, WorkflowStatus.SUCCESS.value]:
            # Message Events and StatuskeyConsumption may also be required
            self.redis_client.delete(self.workflow_data_key)
            self.redis_client.delete(self.workflow_input_key)

    async def async_set_workflow_status(self, status: str, reason: str = None):
        await self.redis_client.aset(
            self.workflow_status_key,
            {"status": status, "reason": reason, "time": time.time()},
            expiration=3600 * 24 * 7,
        )
        if status in [WorkflowStatus.FAILED.value, WorkflowStatus.SUCCESS.value]:
            # Message Events and StatuskeyConsumption may also be required
            await self.redis_client.adelete(self.workflow_data_key)
            await self.redis_client.adelete(self.workflow_input_key)

    def get_workflow_status(self) -> dict | None:
        workflow_status = self.redis_client.get(self.workflow_status_key)
        return workflow_status

    async def async_get_workflow_status(self) -> dict | None:
        workflow_status = await self.redis_client.aget(self.workflow_status_key)
        return workflow_status

    def clear_workflow_status(self):
        self.redis_client.delete(self.workflow_status_key)
        self.redis_client.delete(self.workflow_stop_key)
        self.redis_client.delete(self.workflow_data_key)
        # F052 fix: the round is over, so its id goes with the rest of the round state.
        self.redis_client.delete(self.workflow_run_id_key)
        self._run_id = None

    async def async_clear_workflow_status(self):
        await self.redis_client.adelete(self.workflow_status_key)
        await self.redis_client.adelete(self.workflow_stop_key)
        await self.redis_client.adelete(self.workflow_data_key)
        await self.redis_client.adelete(self.workflow_run_id_key)
        self._run_id = None

    def insert_workflow_response(self, event: dict):
        self.redis_client.rpush(self.workflow_event_key, json.dumps(event), expiration=self.workflow_expire_time)

    def get_workflow_response(self) -> ChatResponse | None:
        response = self.redis_client.lpop(self.workflow_event_key)
        if self.get_workflow_stop():
            self.redis_client.delete(self.workflow_event_key)
            return None
        if response:
            response = ChatResponse(**json.loads(response))
        return response

    async def async_get_workflow_response(self) -> ChatResponse | None:
        response = await self.redis_client.alpop(self.workflow_event_key)
        if await self.async_get_workflow_stop():
            await self.redis_client.adelete(self.workflow_event_key)
            return None
        if response:
            response = ChatResponse(**json.loads(response))
        return response

    def build_chat_response(self, category, category_type, message, extra=None, files=None):
        return ChatResponse(
            user_id=self.user_id,
            chat_id=self.chat_id,
            flow_id=self.workflow_id,
            type=category_type,
            message=message,
            category=category,
            extra=extra,
            files=files,
        )

    def parse_workflow_failed(self, status_info: dict) -> ChatResponse | None:
        if status_info["reason"].find("-- has run more than the maximum number of times") != -1:
            return self.build_chat_response(
                WorkflowEventType.Error.value,
                "over",
                message=WorkFlowNodeRunMaxTimesError(exception=status_info["reason"].split("--")[0]).to_dict(),
            )
        elif status_info["reason"].find("workflow wait user input timeout") != -1:
            return self.build_chat_response(
                WorkflowEventType.Error.value, "over", message=WorkFlowWaitUserTimeoutError().to_dict()
            )
        elif status_info["reason"].find("-- node params is error") != -1:
            return self.build_chat_response(
                WorkflowEventType.Error.value,
                "over",
                message=WorkFlowNodeUpdateError(exception=status_info["reason"].split("--")[0]).to_dict(),
            )
        elif status_info["reason"].find("-- workflow node is update") != -1:
            return self.build_chat_response(
                WorkflowEventType.Error.value,
                "over",
                message=WorkFlowVersionUpdateError(exception=status_info["reason"].split("--")[0]).to_dict(),
            )
        elif status_info["reason"].find("stop by user") != -1:
            return None
        else:
            return self.build_chat_response(
                WorkflowEventType.Error.value, "over", WorkFlowTaskOtherError(exception=status_info["reason"]).to_dict()
            )

    def sync_get_response_until_break(self) -> Iterator[ChatResponse]:
        while True:
            # get workflow status
            status_info = self.get_workflow_status()
            if not status_info:
                yield self.build_chat_response(
                    WorkflowEventType.Error.value,
                    "over",
                    message=WorkFlowTaskOtherError(exception=Exception("workflow status not found")).to_dict(),
                )
                break
            elif status_info["status"] in [WorkflowStatus.FAILED.value, WorkflowStatus.SUCCESS.value]:
                while True:
                    chat_response = self.get_workflow_response()
                    if not chat_response:
                        break
                    yield chat_response
                if status_info["status"] == WorkflowStatus.FAILED.value:
                    error_resp = self.parse_workflow_failed(status_info)
                    if error_resp:
                        yield error_resp
                break
            elif status_info["status"] == WorkflowStatus.INPUT.value:
                while True:
                    chat_response = self.get_workflow_response()
                    if not chat_response:
                        break
                    yield chat_response
                break
            elif (
                status_info["status"] in [WorkflowStatus.WAITING.value, WorkflowStatus.INPUT_OVER.value]
                and time.time() - status_info["time"] > 10
            ):
                # 10s without a status update means the task was published but no
                # worker has picked it up. Two causes need different fixes — no
                # worker consuming the queue at all (start one) vs. every worker
                # busy (retry later) — so classify before reporting.
                if self._has_alive_worker():
                    self.set_workflow_status(WorkflowStatus.FAILED.value, "workflow task execute busy")
                    err_msg = WorkFlowTaskBusyError()
                else:
                    reason = "no alive workflow worker is consuming the queue"
                    self.set_workflow_status(WorkflowStatus.FAILED.value, reason)
                    err_msg = WorkFlowTaskOtherError(exception=Exception(reason))
                # Mark the run stopped too: a worker that picks the task up in the
                # window between the status read above and those writes would
                # otherwise keep executing a run the client already reported failed.
                # dispatch=False — see set_workflow_stop: the run was never picked up,
                # so there is no cached workflow object to stop, and the dispatched
                # task would only clobber the reason we just recorded.
                self.set_workflow_stop(dispatch=False)
                yield self.build_chat_response(WorkflowEventType.Error.value, "over", message=err_msg.to_dict())
                break
            elif time.time() - status_info["time"] > 86400:
                yield self.build_chat_response(
                    WorkflowEventType.Error.value,
                    "over",
                    WorkFlowTaskOtherError(exception=Exception("workflow status not update over 1 day")).to_dict(),
                )
                self.set_workflow_status(WorkflowStatus.FAILED.value, "workflow status not update over 1 day")
                self.set_workflow_stop()
                break
            else:
                chat_response = self.get_workflow_response()
                if not chat_response:
                    time.sleep(1)
                    continue
                yield chat_response

    async def get_response_until_break(self) -> AsyncIterator[ChatResponse]:
        """Continuous accessworkflowright of privacyresponseuntil the end of the run is encountered or pending entry"""
        while True:
            # get workflow status
            status_info = await self.async_get_workflow_status()
            if not status_info:
                yield self.build_chat_response(
                    WorkflowEventType.Error.value,
                    "over",
                    message=WorkFlowTaskOtherError(exception=Exception("workflow status not found")).to_dict(),
                )
                break
            elif status_info["status"] in [WorkflowStatus.FAILED.value, WorkflowStatus.SUCCESS.value]:
                while True:
                    chat_response = await self.async_get_workflow_response()
                    if not chat_response:
                        break
                    yield chat_response
                if status_info["status"] == WorkflowStatus.FAILED.value:
                    error_resp = self.parse_workflow_failed(status_info)
                    if error_resp:
                        yield error_resp
                break
            elif status_info["status"] == WorkflowStatus.INPUT.value:
                while True:
                    chat_response = await self.async_get_workflow_response()
                    if not chat_response:
                        break
                    yield chat_response
                break
            elif (
                status_info["status"] in [WorkflowStatus.WAITING.value, WorkflowStatus.INPUT_OVER.value]
                and time.time() - status_info["time"] > 10
            ):
                # See the sync variant: classify the stall before reporting, or
                # "no worker is running at all" gets misreported as thread exhaustion.
                if await self._has_alive_worker_async():
                    await self.async_set_workflow_status(WorkflowStatus.FAILED.value, "workflow task execute busy")
                    err_msg = WorkFlowTaskBusyError()
                else:
                    reason = "no alive workflow worker is consuming the queue"
                    await self.async_set_workflow_status(WorkflowStatus.FAILED.value, reason)
                    err_msg = WorkFlowTaskOtherError(exception=Exception(reason))
                # dispatch=False — see set_workflow_stop for why.
                await self.async_set_workflow_stop(dispatch=False)
                yield self.build_chat_response(WorkflowEventType.Error.value, "over", message=err_msg.to_dict())
                break
            elif time.time() - status_info["time"] > 86400:
                yield self.build_chat_response(
                    WorkflowEventType.Error.value,
                    "over",
                    message=WorkFlowTaskOtherError(
                        exception=Exception("workflow status not update over 1 day")
                    ).to_dict(),
                )
                await self.async_set_workflow_status(
                    WorkflowStatus.FAILED.value, "workflow status not update over 1 day"
                )
                await self.async_set_workflow_stop()
                break
            else:
                chat_response = await self.async_get_workflow_response()
                if not chat_response:
                    await asyncio.sleep(0.01)
                    continue
                yield chat_response

    def set_user_input(
        self, data: dict, message_id: int = None, message_content: str = None, verify_input: bool = False
    ):
        if self.chat_id and message_id:
            self._bind_current_turn()
            message_db = ChatMessageDao.get_message_by_id(message_id)
            self.update_old_message(data, message_db, message_content, verify_input)
        # Notify Asynchronous Task User Input
        self.redis_client.set(self.workflow_input_key, data, expiration=self.workflow_expire_time)
        return

    async def async_set_user_input(
        self,
        data: dict,
        message_id: int = None,
        message_content: str = None,
        verify_input: bool = False,
        files: list | None = None,
    ):
        if self.chat_id and message_id:
            self._bind_current_turn()
            message_db = await ChatMessageDao.aget_message_by_id(message_id)
            await self.async_update_old_message(data, message_db, message_content, verify_input, files)
        # Notify Asynchronous Task User Input
        await self.redis_client.aset(self.workflow_input_key, data, expiration=self.workflow_expire_time)
        return

    def _bind_current_turn(self) -> None:
        """输入由 API 进程落库，轮次由 Worker 持有；按 Redis 中的轮次 ID 补齐关联。"""
        if self.business_turn_id:
            return
        turn_id = self.run_id
        if turn_id and ChatTurnService.get_turn(turn_id) is not None:
            self.business_turn_id = turn_id

    @staticmethod
    def _verify_input_schema(input_schema_message: dict, user_input: dict):
        """Verify that the user input matches the input schema"""
        node_id = input_schema_message["node_id"]
        if node_id not in user_input:
            raise ServerError(msg="node_id not found in user input")
        user_input = user_input[node_id]
        input_schema = input_schema_message["input_schema"]
        if input_schema["tab"] == "form_input":
            user_input_keys = dict.fromkeys(user_input.keys())
            for key_info in input_schema["value"]:
                key = key_info["key"]
                if key not in user_input:
                    raise ServerError(msg=f"key {key} not found in user input")
                user_input_keys.pop(key)
            if user_input_keys:
                raise ServerError(msg=f"extra key {list(user_input_keys.keys())} found in user input")
        else:
            if input_schema["key"] not in user_input:
                raise ServerError(msg=f"key {input_schema['key']} not found in user input")

    @classmethod
    def _update_old_message(
        cls,
        user_input: dict,
        message_db: ChatMessage,
        message_content: str,
        verify_input: bool = False,
        files: list | None = None,
    ):
        """
        if ChatResponse is not None: add new message
        if ChatMessage is not None: update old message
        return ChatResponse | None, ChatMessage | None
        """
        if not message_db:
            if verify_input:
                raise ServerError(msg="message info not found by message id")
            return None
        # Update the input and selection of the user in the output to be entered message
        old_message = json.loads(message_db.message)
        if message_db.category == WorkflowEventType.OutputWithInput.value:
            old_message["hisValue"] = user_input[old_message["node_id"]][old_message["key"]]
        elif message_db.category == WorkflowEventType.OutputWithChoose.value:
            old_message["hisValue"] = user_input[old_message["node_id"]][old_message["key"]]
        elif message_db.category == WorkflowEventType.UserInput.value:
            if verify_input:
                cls._verify_input_schema(old_message, user_input)
            user_input = user_input[old_message["node_id"]]

            # If the front-end passes user input, the front-end content is used.
            if message_content:
                user_input_message = message_content
            # Instructions are form inputs
            elif old_message["input_schema"]["tab"] == "form_input":
                user_input_message = ""
                for key_info in old_message["input_schema"]["value"]:
                    user_input_message += f"{key_info['value']}:{user_input.get(key_info['key'], '')}\n"
            else:
                # Description Dialog Input, Uploaded file information needs to be added, It is related to the data structure of the input node.
                user_input_message = user_input[old_message["input_schema"]["key"]]
                dialog_files_content = user_input.get("dialog_files_content", [])
                for one in dialog_files_content:
                    user_input_message += f"\n{os.path.basename(one).split('?')[0]}"
            return ChatResponse(
                message=user_input_message,
                category="question",
                # Carry the attachments as data, not just as filenames appended
                # to the text — that is what lets them render and be kept.
                files=files or [],
            ), None
        message_db.message = json.dumps(old_message, ensure_ascii=False)
        return None, message_db

    def update_old_message(
        self,
        user_input: dict,
        message_db: ChatMessage,
        message_content: str,
        verify_input: bool = False,
        files: list | None = None,
    ):
        chat_response, message = self._update_old_message(user_input, message_db, message_content, verify_input, files)
        if chat_response:
            self.save_chat_message(chat_response)
            return
        if message:
            ChatMessageDao.update_message_model(message)

    async def async_update_old_message(
        self,
        user_input: dict,
        message_db: ChatMessage,
        message_content: str,
        verify_input: bool = False,
        files: list | None = None,
    ):
        chat_response, message = self._update_old_message(user_input, message_db, message_content, verify_input, files)
        if chat_response:
            self.save_chat_message(chat_response)
            return
        if message:
            await ChatMessageDao.aupdate_message_model(message)

    def get_user_input(self) -> dict | None:
        ret = self.redis_client.get(self.workflow_input_key)
        if ret:
            self.redis_client.delete(self.workflow_input_key)
        return ret

    def set_workflow_stop(self, dispatch: bool = True):
        """Record the stop intent; optionally also dispatch ``stop_workflow``.

        The stop key alone is what stops a run cooperatively — a running workflow
        polls it in ``send_chat_response``. The dispatched task only adds a
        busy-wait on a workflow object parked in this worker's ``_global_workflow``,
        which does not exist for a run that was never picked up. Pass
        ``dispatch=False`` when stopping a run that never started: the task would be
        published to the very queue we just found nobody consuming, and once it
        eventually runs it finds no cached workflow object and overwrites the
        recorded failure reason with "workflow stop by user" — which
        ``parse_workflow_failed`` maps to None, leaving the user a silent close
        instead of the error we reported.
        """
        self.redis_client.set(self.workflow_stop_key, 1, expiration=3600 * 24)
        if dispatch:
            from bisheng.worker.workflow.tasks import stop_workflow

            stop_workflow.delay(self.unique_id, self.workflow_id, self.chat_id, self.user_id)

    async def async_set_workflow_stop(self, dispatch: bool = True):
        """Async counterpart of :meth:`set_workflow_stop`. See it for ``dispatch``."""
        await self.redis_client.aset(self.workflow_stop_key, 1, expiration=3600 * 24)
        if dispatch:
            from bisheng.worker.workflow.tasks import stop_workflow

            stop_workflow.delay(self.unique_id, self.workflow_id, self.chat_id, self.user_id)

    def get_workflow_stop(self) -> bool | None:
        """In order to stop in timeworkflow, Do not cache memory"""
        return self.redis_client.get(self.workflow_stop_key) == 1

    async def async_get_workflow_stop(self) -> bool | None:
        """In order to stop in timeworkflow, Do not cache memory"""
        return await self.redis_client.aget(self.workflow_stop_key) == 1

    def mint_run_id(self) -> str:
        """Start a new round: give it an id, remember it, and publish it for the round's
        ``continue_workflow`` (a different process) to pick up.

        Prefixed with ``unique_id`` on purpose: the log format is
        ``trace={extra[trace_id]}`` (config.yaml), so ``grep <chat_id>`` still finds every
        round of a chat. It stays a single opaque ``keyword`` to Elasticsearch.
        """
        self._run_id = f"{self.unique_id}:{uuid.uuid4().hex}"
        self.redis_client.set(self.workflow_run_id_key, self._run_id, expiration=self.workflow_expire_time)
        return self._run_id

    @property
    def run_id(self) -> str:
        """This round's id, or ``unique_id`` when there is none to be had.

        The fallback is deliberate and load-bearing: a round whose key expired (or a
        caller that never minted -- evaluation, older rows) degrades to the legacy
        chat-scoped id instead of raising, which at worst re-merges rounds the way
        everything did before the split. Read at most once per instance, so the node
        callbacks that ask for it on every row pay nothing after the first ask.
        """
        if self._run_id is None:
            stored = self.redis_client.get(self.workflow_run_id_key) if self.workflow_run_id_key else None
            self._run_id = stored or self.unique_id
        return self._run_id

    def send_chat_response(self, chat_response: ChatResponse):
        """Send a chat message"""
        self.insert_workflow_response(chat_response.dict())

        # Determine if it needs to be stoppedworkflow, Don't judge when streaming, queries are too frequent and can't be stoppedworkflow
        if chat_response.category == WorkflowEventType.StreamMsg.value:
            return
        if self.workflow and self.get_workflow_stop():
            self.workflow.stop()

    def save_chat_message(
        self,
        chat_response: ChatResponse,
        source_documents=None,
        citation_registry_items: list[CitationRegistryItemSchema] | None = None,
    ) -> int | str | None:
        """save chat message to database
        return message id
        """
        # Filter BEFORE anything reads the text. The websocket guard runs in the
        # API process and only rewrites the frame, so without this the history
        # kept the unfiltered answer -- see `answer_filter` for why this is a
        # separate implementation, and for what it does and does not cover.
        #
        # Filtering here rather than at the call sites means it also applies to
        # the citation extraction below, which would otherwise resolve offsets
        # against text the user never saw.
        if chat_response.category != WorkflowEventType.UserInput.value:
            chat_response.message = filter_chat_response_message(chat_response.message, self.tenant_id)

        if not self.chat_id:
            answer_text = self._extract_message_text(chat_response.message)
            items = self._resolve_citation_items(
                answer_text=answer_text,
                source_documents=source_documents,
                citation_registry_items=citation_registry_items,
            )
            save_message_citations_sync(
                message_id=None,
                items=items,
                chat_id=self.chat_id,
                flow_id=self.workflow_id,
            )
            # Generate a fake messageidPrevent duplicate front-end message rendering
            return uuid.uuid4().hex

        # Judgment traceability
        if source_documents:
            result = {}
            extra = {}
            if isinstance(source_documents, Document):
                result = source_documents
            source, result = sync_judge_source(result, source_documents, self.chat_id, extra)
            chat_response.source = source
            chat_response.extra = json.dumps(extra, ensure_ascii=False)

        # Attachments the user sent become permanent here, not at upload time —
        # uploads sit in the temp bucket, which clears itself every 3 days.
        # Only the user's own files: what the workflow produced is out of scope.
        # Keyed on category, not is_bot: that field defaults to True, so a
        # question would have been skipped.
        if chat_response.category == "question":
            promote_chat_attachments_sync(chat_response.files, self.user_id)

        message = ChatMessageDao.insert_one(
            ChatMessage(
                user_id=self.user_id,
                chat_id=self.chat_id,
                flow_id=self.workflow_id,
                type=chat_response.type,
                is_bot=chat_response.is_bot,
                source=chat_response.source,
                message=chat_response.message
                if isinstance(chat_response.message, str)
                else json.dumps(chat_response.message, ensure_ascii=False),
                extra=chat_response.extra,
                category=chat_response.category,
                files=json.dumps(chat_response.files, ensure_ascii=False),
                turn_id=self.business_turn_id,
            )
        )

        if self.business_turn_id and chat_response.category == QUESTION_CATEGORY:
            ChatTurnService.record_question(
                turn_id=self.business_turn_id,
                question_message_id=message.id,
                question_text=self._extract_message_text(chat_response.message),
            )
        elif self.business_turn_id and chat_response.category in {
            "answer",
            "stream_msg",
            "output_msg",
            "output_with_input_msg",
            "output_with_choose_msg",
        }:
            self.last_final_message_id = message.id
            self.last_final_answer_text = self._extract_message_text(chat_response.message)

        answer_text = self._extract_message_text(chat_response.message)
        items = self._resolve_citation_items(
            answer_text=answer_text,
            source_documents=source_documents,
            citation_registry_items=citation_registry_items,
        )
        save_message_citations_sync(
            message_id=message.id,
            items=items,
            chat_id=self.chat_id,
            flow_id=self.workflow_id,
        )

        # If the document is traceable, handle the recallchunk
        if chat_response.source not in [0, 4]:
            thread_pool.submit(
                f"workflow_source_document_{self.chat_id}",
                sync_process_source_document,
                source_documents,
                self.chat_id,
                message.id,
                chat_response.message.get("msg"),
            )

        # Determine if a new session is needed
        if not self.create_session and chat_response.category != WorkflowEventType.UserInput.value:
            # Insert a new session without session data
            if not MessageSessionDao.get_one(self.chat_id):
                db_workflow = FlowDao.get_flow_by_id(self.workflow_id)
                self.new_session = MessageSessionDao.insert_one(
                    MessageSession(
                        chat_id=self.chat_id,
                        flow_id=self.workflow_id,
                        flow_name=db_workflow.name,
                        flow_type=FlowType.WORKFLOW.value,
                        user_id=self.user_id,
                    )
                )
                thread_pool.submit(
                    f"workflow_generate_title_{self.chat_id}", self.generate_session_title, message.message
                )

                # RecordTelemetryJournal
                telemetry_service.log_event_sync(
                    user_id=self.user_id,
                    event_type=BaseTelemetryTypeEnum.NEW_MESSAGE_SESSION,
                    trace_id=trace_id_var.get(),
                    event_data=NewMessageSessionEventData(
                        session_id=self.chat_id,
                        app_id=self.workflow_id,
                        source=self.source,
                        app_name=db_workflow.name,
                        app_type=ApplicationTypeEnum.WORKFLOW,
                    ),
                )

            self.create_session = True

        return message.id

    @staticmethod
    def _read_extra(raw_extra: str | None) -> dict:
        """The `extra` column as a dict. Unreadable values read as empty rather than
        raised on: losing a run over a column nobody can parse is worse than losing
        whatever was in it."""
        try:
            extra = json.loads(raw_extra) if raw_extra else {}
        except ValueError:
            logger.warning("chat message extra is not json, treating it as empty: {!r}", raw_extra)
            extra = {}
        return extra if isinstance(extra, dict) else {}

    @classmethod
    def merge_extra(cls, raw_extra: str | None, payload: dict) -> str:
        """Read-merge-write on the shared `extra` column (F067 decision 3, pitfall 10).

        The column already holds other writers' keys -- citation provenance lands there
        on answer rows -- so it can only ever be merged into, never replaced.
        """
        extra = cls._read_extra(raw_extra)
        extra.update(payload)
        return json.dumps(extra, ensure_ascii=False)

    def _own_question_messages(self, limit: int) -> list:
        """This chat's question messages that belong to the caller, newest first.

        The ownership filter lives here rather than at each call site because the WS
        layer never checks that a chat id is the caller's (pitfall 11): a chat we do not
        own must yield nothing to read *and* nothing to write to.
        """
        rows = ChatMessageDao.get_messages_by_chat_id(self.chat_id, [QUESTION_CATEGORY], limit)
        mine = [row for row in rows if row.user_id == self.user_id]
        if len(mine) != len(rows):
            logger.warning("chat {} has question messages belonging to another user, ignoring them", self.chat_id)
        return mine

    def read_input_outputs(self) -> dict:
        """F067: the first round's input node outputs recorded for this chat ({} if none).

        The read side of `save_input_outputs`, kept next to it so that the namespace and
        the "which message" rule have one home.

        **Not just the newest question message.** Every follow-up round writes its own
        question row and none of them carries the outputs (only the first round's node
        run does), so on round 3 the newest row is round 2's -- and reading that one
        would report "no inputs on record" and quietly turn the round back into a first
        round, asking for the form again (AC-16). So: the newest row that *has* them.
        Looking back is bounded by the number of follow-up rounds in the session, and
        running out of window degrades to the same first-round path rather than to a
        wrong value, with a warning to say so.
        """
        for row in self._own_question_messages(RECORDED_OUTPUTS_LOOKBACK):  # newest first
            outputs = self._read_extra(row.extra).get(INPUT_OUTPUTS_EXTRA_KEY)
            if outputs:
                return outputs
        logger.info(
            "no recorded input outputs within {} question messages on chat={}", RECORDED_OUTPUTS_LOOKBACK, self.chat_id
        )
        return {}

    def save_input_outputs(self, node_id: str) -> None:
        """F067: put the form input node's outputs on this round's question message.

        This is where a follow-up round gets the first round's outputs from (AC-16):
        the graph state that produced them dies with the round's `Workflow` object.

        Read from the variable pool, not from the node's log -- `InputNode.parse_log`
        drops the file field's temp-KB key, and one key short is enough for the next
        round to miss the reuse and re-download + re-parse the files (AC-04).
        """
        workflow = self.workflow
        outputs = (workflow.graph_engine.graph_state.variables_pool.get(node_id) or {}) if workflow else {}
        if not outputs:
            logger.warning("no input outputs to record for node={} chat={}", node_id, self.chat_id)
            return
        # The round's own question message: written when the user submitted the form,
        # and the only question on this chat that can be newer (AC-11: same chat runs
        # one round at a time).
        # The round's own question message: normally the one this very round just wrote,
        # so the ownership rule in `_own_question_messages` changes nothing; it matters
        # only when the round left no row of its own (no message id on the submission).
        rows = self._own_question_messages(1)
        if not rows:
            logger.warning("no own question message to record input outputs on chat={}", self.chat_id)
            return
        message = rows[0]
        message.extra = self.merge_extra(message.extra, {INPUT_OUTPUTS_EXTRA_KEY: outputs})
        ChatMessageDao.update_message_model(message)

    def save_follow_up_question(self, question: str) -> int | str | None:
        """F067: persist this round's follow-up question (decision 5).

        A follow-up round skips the input node's interrupt, so no `UserInput` event
        fires and the existing write branch never runs. Without this the question is
        missing from the conversation, and the next round reads back a history that
        stops at the previous answer.
        """
        return self.save_chat_message(self.build_chat_response(QUESTION_CATEGORY, "over", question))

    @staticmethod
    def _resolve_citation_items(
        answer_text: str,
        source_documents=None,
        citation_registry_items: list[CitationRegistryItemSchema] | None = None,
    ) -> list[CitationRegistryItemSchema]:
        items = list(citation_registry_items or [])
        if not items and source_documents:
            documents = source_documents if isinstance(source_documents, list) else [source_documents]
            items = collect_rag_citation_registry_items(documents)
        return select_registry_items_for_persistence(items, answer_text)

    @staticmethod
    def _extract_message_text(message: str | dict | list | None) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            msg = message.get("msg")
            if isinstance(msg, str):
                return msg
            return json.dumps(message, ensure_ascii=False)
        if message is None:
            return ""
        return json.dumps(message, ensure_ascii=False)

    def generate_session_title(self, answer: str):
        if not self.new_session:
            return
        if self.new_session.name:
            return
        self.new_session.name = "New Chat"

        question = ""
        input_message = ChatMessageDao.get_messages_by_chat_id(self.chat_id, [WorkflowEventType.UserInput.value], 1)
        if input_message:
            question = input_message[0].message

        llm_conf = LLMService.get_workbench_llm_sync(tenant_id=self.tenant_id)
        if not llm_conf or not llm_conf.chat_title_llm or not llm_conf.chat_title_llm.id:
            return
        llm = LLMService.get_bisheng_llm_sync(
            model_id=llm_conf.chat_title_llm.id,
            app_id=ApplicationTypeEnum.DAILY_CHAT.value,
            app_name="workflow_chat_title",
            app_type=ApplicationTypeEnum.DAILY_CHAT,
            user_id=self.user_id,
        )
        title = generate_conversation_title_sync(question=question, llm=llm, answer=answer)
        MessageSessionDao.update_session_name_sync(self.new_session.chat_id, title)
        self.new_session.name = title

    def on_node_start(self, data: NodeStartData):
        """node start event"""
        logger.debug(f"node start: {data}")
        self.send_chat_response(
            ChatResponse(
                message=data.dict(),
                category=WorkflowEventType.NodeRun.value,
                type="start",
                flow_id=self.workflow_id,
                chat_id=self.chat_id,
            )
        )
        # F052: persist node-run record for the transaction monitor (best-effort, AC-04).
        # Row key = this ROUND's id (what the monitor lists as the transaction trace);
        # data.unique_id is the per-execution uuid (pairs this start with its end --
        # do NOT confuse the two).
        NodeRunDao.insert_event(
            unique_id=self.run_id,
            exec_id=data.unique_id,
            chat_id=self.chat_id,
            flow_id=self.workflow_id,
            node_id=data.node_id,
            node_name=data.name,
            event_type="start",
            status="running",
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )

    def on_node_end(self, data: NodeEndData):
        """node end event"""
        logger.debug(f"node end: {data}")
        self.send_chat_response(
            ChatResponse(
                message=data.dict(),
                category=WorkflowEventType.NodeRun.value,
                type="end",
                flow_id=self.workflow_id,
                chat_id=self.chat_id,
            )
        )
        # F052: persist node-run record for the transaction monitor (best-effort, AC-04).
        # Row key = this ROUND's id; data.unique_id pairs start/end (per-execution uuid).
        NodeRunDao.insert_event(
            unique_id=self.run_id,
            exec_id=data.unique_id,
            chat_id=self.chat_id,
            flow_id=self.workflow_id,
            node_id=data.node_id,
            node_name=data.name,
            event_type="end",
            status="failed" if data.reason else "success",
            reason=data.reason,
            input_data=data.input_data,
            output_data=data.log_data,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )
        # F067: the form input node has now run (the user's submission resumed this run)
        # -- record its outputs for the follow-up rounds to reuse (AC-16, decision 3).
        if data.node_id and data.node_id == self._form_input_node_id:
            self.save_input_outputs(data.node_id)

    def on_user_input(self, data: UserInputData):
        """user input event"""
        logger.debug(f"user input: {data}")
        # F067: the engine pauses *before* this node runs, so this is the only place that
        # can tell a form-mode input node (whose outputs the follow-up rounds reuse) from
        # a dialog-mode one (untouched by this feature, AC-13).
        if getattr(data.input_schema, "tab", None) == "form_input":
            self._form_input_node_id = data.node_id
        chat_response = ChatResponse(
            message=data.dict(),
            category=WorkflowEventType.UserInput.value,
            type="over",
            flow_id=self.workflow_id,
            chat_id=self.chat_id,
        )
        msg_id = self.save_chat_message(chat_response)
        if msg_id:
            chat_response.message_id = msg_id
        self.send_chat_response(chat_response)

    def on_guide_word(self, data: GuideWordData):
        """guide word event"""
        logger.debug(f"guide word: {data}")
        self.send_chat_response(
            ChatResponse(
                message=data.dict(),
                category=WorkflowEventType.GuideWord.value,
                type="over",
                flow_id=self.workflow_id,
                chat_id=self.chat_id,
            )
        )

    def on_guide_question(self, data: GuideQuestionData):
        """guide question event"""
        logger.debug(f"guide question: {data}")
        self.send_chat_response(
            ChatResponse(
                message=data.dict(),
                category=WorkflowEventType.GuideQuestion.value,
                type="over",
                flow_id=self.workflow_id,
                chat_id=self.chat_id,
            )
        )

    def on_output_msg(self, data: OutputMsgData):
        logger.debug(f"output msg: {data}")
        chat_response = ChatResponse(
            message=data.dict(exclude={"source_documents", "citation_registry_items"}),
            category=WorkflowEventType.OutputMsg.value,
            extra="",
            type="over",
            flow_id=self.workflow_id,
            chat_id=self.chat_id,
            files=data.files,
            citations=data.citation_registry_items,
            citation_registry_items=data.citation_registry_items,
        )
        msg_id = self.save_chat_message(
            chat_response,
            source_documents=data.source_documents,
            citation_registry_items=data.citation_registry_items,
        )
        if msg_id:
            chat_response.message_id = msg_id
        self.send_chat_response(chat_response)

    def on_stream_msg(self, data: StreamMsgData):
        logger.debug(f"stream msg: {data}")
        self.send_chat_response(
            ChatResponse(
                message=data.dict(),
                category=WorkflowEventType.StreamMsg.value,
                extra="",
                type="stream",
                flow_id=self.workflow_id,
                chat_id=self.chat_id,
            )
        )

    def on_stream_over(self, data: StreamMsgOverData):
        logger.debug(f"stream over: {data}")
        # 分享链接前缀剥离：MinIO 模式下去掉 "http://<sharepoint>"，让前端按同源相对路径解析；
        # 本地存储模式下分享链接本身就是相对路径，这里自然成为空操作。
        # 走存储实例而不是直接读 minio 配置，切换后端时行为自动一致。
        from bisheng.core.storage.minio.minio_manager import get_minio_storage_sync

        share_host = get_minio_storage_sync().get_minio_share_host().replace("https://", "").replace("http://", "")
        if share_host:
            data.msg = data.msg.replace(f"http://{share_host}", "")
        chat_response = ChatResponse(
            message=data.dict(exclude={"source_documents", "citation_registry_items"}),
            category=WorkflowEventType.StreamMsg.value,
            extra="",
            type="end",
            flow_id=self.workflow_id,
            chat_id=self.chat_id,
            citations=data.citation_registry_items,
            citation_registry_items=data.citation_registry_items,
        )
        msg_id = self.save_chat_message(
            chat_response,
            source_documents=data.source_documents,
            citation_registry_items=data.citation_registry_items,
        )
        if msg_id:
            chat_response.message_id = msg_id
        self.send_chat_response(chat_response)

    def on_output_choose(self, data: OutputMsgChooseData):
        logger.debug(f"output choose: {data}")
        chat_response = ChatResponse(
            message=data.dict(exclude={"source_documents", "citation_registry_items"}),
            category=WorkflowEventType.OutputWithChoose.value,
            extra="",
            type="over",
            flow_id=self.workflow_id,
            chat_id=self.chat_id,
            files=data.files,
            citations=data.citation_registry_items,
            citation_registry_items=data.citation_registry_items,
        )
        msg_id = self.save_chat_message(
            chat_response,
            source_documents=data.source_documents,
            citation_registry_items=data.citation_registry_items,
        )
        if msg_id:
            chat_response.message_id = msg_id
        self.send_chat_response(chat_response)

    def on_output_input(self, data: OutputMsgInputData):
        logger.debug(f"output input: {data}")
        chat_response = ChatResponse(
            message=data.dict(exclude={"source_documents", "citation_registry_items"}),
            category=WorkflowEventType.OutputWithInput.value,
            extra="",
            type="over",
            flow_id=self.workflow_id,
            chat_id=self.chat_id,
            files=data.files,
            citations=data.citation_registry_items,
            citation_registry_items=data.citation_registry_items,
        )
        msg_id = self.save_chat_message(
            chat_response,
            source_documents=data.source_documents,
            citation_registry_items=data.citation_registry_items,
        )
        if msg_id:
            chat_response.message_id = msg_id
        self.send_chat_response(chat_response)
