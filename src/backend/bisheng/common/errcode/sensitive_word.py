from .base import BaseErrorCode


# Sensitive word / guardrail module error codes, module code: 112
class SensitiveWordInputBlockedError(BaseErrorCode):
    """Fallback message for API callers; the user-facing reply is the policy auto_reply."""

    Code: int = 11201
    Msg: str = "Message blocked: input contains sensitive words"


class SensitiveWordOutputTruncatedError(BaseErrorCode):
    Code: int = 11202
    Msg: str = "Output truncated: generated content contains sensitive words"
