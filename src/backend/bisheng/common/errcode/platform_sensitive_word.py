from .base import BaseErrorCode


# Platform-level sensitive word library, module code: 113 (F059).
#
# Hit interception itself reuses F056's 11201 / 11202 (SensitiveWordInputBlockedError /
# SensitiveWordOutputTruncatedError) — the platform layer only changes *whose* policy
# wins, not what a hit means on the wire. Only import-side failures live here.
class PlatformWordFileParseError(BaseErrorCode):
    """Import file could not be parsed, or yielded no usable word."""

    Code: int = 11301
    Msg: str = "Import failed: the file could not be parsed or contains no valid words"


class PlatformWordNotFoundError(BaseErrorCode):
    """The word row an operation targeted does not exist (deleted concurrently)."""

    Code: int = 11302
    Msg: str = "The word does not exist"
