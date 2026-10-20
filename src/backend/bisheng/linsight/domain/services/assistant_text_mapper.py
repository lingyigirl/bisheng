"""Map assistant text independently of reasoning and tool-call frames."""

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, AIMessageChunk

from bisheng_langchain.linsight.event import ExecStep


@dataclass
class TextSegment:
    call_id: str
    message_id: str
    namespace: str | None
    text: str = ""
    closed: bool = False
    digest: str = ""


def merge_text_output(previous: dict, current: dict) -> dict:
    """Shared delta/snapshot/end semantics for persisted step projections."""
    mode = (current.get("extra_info") or {}).get("text_mode", "delta")
    output = current.get("output") or ""
    if mode == "delta":
        output = (previous.get("output") or "") + output
    elif mode == "end":
        output = previous.get("output") or ""
    return {
        **previous,
        **current,
        "output": output,
        "started_at": previous.get("started_at", previous.get("timestamp", current.get("timestamp"))),
        "extra_info": {
            **(previous.get("extra_info") or {}),
            **(current.get("extra_info") or {}),
            "text_mode": "snapshot",
        },
    }


class AssistantTextMapper:
    def __init__(self, svid: str, run_token: str):
        self.svid = svid
        self.run_token = run_token
        self.segments: dict[tuple[str, str], TextSegment] = {}
        self.active: dict[str, TextSegment] = {}
        self.sequence = 0
        self.last_answer_step_id: str | None = None
        self.last_answer_text: str | None = None

    @staticmethod
    def text(message: Any) -> str:
        content = message.content
        if isinstance(content, str):
            return content
        return "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
            if isinstance(block, str) or (isinstance(block, dict) and block.get("type") in {"text", "output_text"})
        )

    def frame(self, segment: TextSegment, output: str, mode: str) -> ExecStep:
        info = {
            "text_mode": mode,
            "message_id": segment.message_id,
            "stream_received_at": monotonic(),
            "text_frame_id": uuid4().hex,
        }
        if segment.namespace:
            info["namespace"] = segment.namespace
        return ExecStep(
            task_id=self.svid,
            call_id=segment.call_id,
            name="assistant",
            call_reason="",
            step_type="assistant",
            status="end" if mode == "end" else "start",
            output=output,
            extra_info=info,
        )

    def close(self, segment: TextSegment) -> list[ExecStep]:
        if segment.closed:
            return []
        segment.closed = True
        segment.digest = sha256(segment.text.encode()).hexdigest()
        if not segment.namespace:
            self.last_answer_step_id = segment.call_id
            self.last_answer_text = segment.text
        return [self.frame(segment, "", "end")]

    def message(self, message: Any, namespace: str | None, metadata: dict | None = None) -> list[ExecStep]:
        if not isinstance(message, AIMessage):
            return []
        text = self.text(message)
        key = namespace or ""
        active = self.active.get(key)
        mid = message.id or str((metadata or {}).get("langgraph_step", ""))
        if not mid:
            mid = active.message_id if active and not active.closed else f"anonymous-{self.sequence}"
        identity = (key, mid)
        segment = self.segments.get(identity)
        events = []
        if segment is None and text:
            if active and not active.closed:
                events.extend(self.close(active))
            if active and active.closed:
                active.text = ""
            self.sequence += 1
            token = sha256(f"{key}:{mid}".encode()).hexdigest()[:16]
            segment = TextSegment(f"assistant:{self.svid}:{self.run_token}:{token}", mid, namespace)
            self.segments[identity] = segment
            self.active[key] = segment
        if segment is not None and not segment.closed:
            if isinstance(message, AIMessageChunk):
                if text:
                    segment.text += text
                    events.append(self.frame(segment, text, "delta"))
                if (message.response_metadata or {}).get("finish_reason"):
                    events.extend(self.close(segment))
            else:
                if text != segment.text:
                    segment.text = text
                    events.append(self.frame(segment, text, "snapshot"))
                events.extend(self.close(segment))
        elif segment is not None and not isinstance(message, AIMessageChunk) and text:
            digest = sha256(text.encode()).hexdigest()
            if digest != segment.digest:
                segment.digest = digest
                events.extend([self.frame(segment, text, "snapshot"), self.frame(segment, "", "end")])
                if self.active.get(key) is segment:
                    segment.text = text
                    if not namespace:
                        self.last_answer_step_id, self.last_answer_text = segment.call_id, text
        if getattr(message, "tool_calls", None) and not namespace:
            self.last_answer_step_id = None
            self.last_answer_text = None
        return events

    def finish(self) -> list[ExecStep]:
        return [event for segment in self.active.values() for event in self.close(segment)]
