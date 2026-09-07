from dataclasses import dataclass
from enum import Enum


class WorkType(Enum):
    GPTS = "assistant"
    WORKFLOW = "workflow"


class IgnoreException(Exception):
    """Exception that does not require traceback logging."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


@dataclass
class OutputGuardResult:
    """Outcome of guarding one streamed output chunk (F056).

    ``safe_text`` is the complete text to emit for this chunk — on a block hit it
    already includes the policy auto_reply close-out. Produced by the API-layer
    guard factory (``api/services/guardrail.py``) and consumed by the common/chat
    clients, which never import domain services themselves (constitution C1).
    """

    safe_text: str = ''
    blocked: bool = False
