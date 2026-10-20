"""Managed-agent errors, module code 121 (F078).

Registered in the v2.7.0 release contract's module table (12101–12114). One code
per rejection the caller can act on: registration editor, invoke caller, health
operator and scheduler each fix a different thing.

Error-code choices that deliberately reuse existing families:

- **11809** answers "not visible / cross-tenant" on the key-authenticated
  ``/api/v2`` invoke face (anti-enumeration, same as F076/F077). ``12101`` is
  reserved for the v1 management face.
- **11807** is a missing scope, **11811** an unregistered endpoint, **11817** a
  caller-supplied identity — all handled by F076 before these classes.
- **11914** (file input not schedulable) and F077's 119xx remain the scheduler's
  own codes; F078 only adds the managed-agent-specific codes below.

Wire format: on the key-authenticated ``/api/v2`` face these errors answer with
their real HTTP status (``open_api_call`` marker); on ``/api/v1`` they keep the
platform's HTTP-200 envelope so the console's existing interceptors behave
exactly as before (design 决策 9 / §4.4).
"""

from .base import BaseErrorCode


class ManagedAgentErrorBase(BaseErrorCode):
    """Common base carrying the real HTTP status used on the ``/api/v2`` face."""

    HttpStatus: int = 400


class ManagedAgentNotFoundError(ManagedAgentErrorBase):
    """Unknown or not-visible managed agent on the v1 management face."""

    Code: int = 12101
    Msg: str = "Managed agent not found or not accessible"
    HttpStatus: int = 404


class ManagedAgentConfigInvalidError(ManagedAgentErrorBase):
    """Registration/invoke configuration breaks a rule."""

    Code: int = 12102
    Msg: str = "Invalid managed agent configuration"


class ManagedAgentDuplicateError(ManagedAgentErrorBase):
    """Name or canonical endpoint is already registered in this tenant."""

    Code: int = 12103
    Msg: str = "Managed agent name or endpoint already exists"
    HttpStatus: int = 409


class ManagedAgentProtocolUnsupportedError(ManagedAgentErrorBase):
    """Source/protocol combination is not supported in this release."""

    Code: int = 12104
    Msg: str = "Unsupported managed agent source or protocol"


class ManagedAgentUnavailableError(ManagedAgentErrorBase):
    """The agent is draft/offline/disabled and cannot be invoked."""

    Code: int = 12105
    Msg: str = "Managed agent is not online"


class ManagedAgentUpstreamError(ManagedAgentErrorBase):
    """The third-party agent returned 4xx/5xx or timed out."""

    Code: int = 12106
    Msg: str = "Managed agent upstream call failed or timed out"
    HttpStatus: int = 502


class ManagedAgentCredentialInvalidError(ManagedAgentErrorBase):
    """The outbound credential is missing, malformed or cannot be decrypted."""

    Code: int = 12107
    Msg: str = "Managed agent credential is missing or invalid"
    HttpStatus: int = 502


class ManagedAgentHealthCheckFailedError(ManagedAgentErrorBase):
    """The health check failed; the caller must not treat it as success."""

    Code: int = 12108
    Msg: str = "Managed agent health check failed"
    HttpStatus: int = 502


class ManagedAgentTaxonomyInvalidError(ManagedAgentErrorBase):
    """The scenario/industry code does not exist or is disabled."""

    Code: int = 12109
    Msg: str = "Invalid managed agent taxonomy code"


class ManagedAgentInUseError(ManagedAgentErrorBase):
    """The agent still has enabled scheduled tasks and cannot be deleted."""

    Code: int = 12110
    Msg: str = "Managed agent has enabled scheduled tasks"
    HttpStatus: int = 409


class ManagedAgentOutboundRejectedError(ManagedAgentErrorBase):
    """The outbound URL failed SSRF/allowlist validation."""

    Code: int = 12111
    Msg: str = "Managed agent outbound address rejected"
    HttpStatus: int = 403


class ManagedAgentRateLimitedError(ManagedAgentErrorBase):
    """Per-tenant/agent invocation rate limit exceeded."""

    Code: int = 12112
    Msg: str = "Managed agent invocation rate limit exceeded"
    HttpStatus: int = 429


class ManagedAgentUpstreamResponseInvalidError(ManagedAgentErrorBase):
    """The upstream response was invalid or exceeded the configured byte cap."""

    Code: int = 12113
    Msg: str = "Managed agent upstream response invalid or too large"
    HttpStatus: int = 502


class ManagedAgentInvokePayloadInvalidError(ManagedAgentErrorBase):
    """The invoke body carried fields outside the strict whitelist."""

    Code: int = 12114
    Msg: str = "Managed agent invoke payload not allowed"
