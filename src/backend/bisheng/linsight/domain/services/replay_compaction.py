"""Lossless adjacent thinking-delta compaction for the opt-in UI snapshot."""


class ReplayCompactor:
    def __init__(self):
        self.events = []
        self._identity = None
        self._parts = []

    def _flush(self):
        if self._parts:
            self.events[-1]["data"]["output"] = "".join(self._parts)
        self._parts = []

    def append(self, event):
        data = event.get("data") or {}
        identity = (
            (data["call_id"], event.get("execution_epoch", data.get("execution_epoch", 0)))
            if event.get("event_type") == "task_execute_step"
            and data.get("step_type") == "thinking"
            and data.get("call_id")
            else None
        )
        if identity is not None and identity == self._identity:
            previous = self.events[-1]
            started = previous["data"].get("started_at", previous["data"].get("timestamp"))
            previous.update(event)
            previous["data"] = {**data, "started_at": started}
            self._parts.append(data.get("output") or "")
            return
        self._flush()
        self._identity = identity
        self.events.append({**event, "data": dict(data)})
        if identity is not None:
            self._parts = [data.get("output") or ""]

    def finish(self):
        self._flush()
        return self.events
