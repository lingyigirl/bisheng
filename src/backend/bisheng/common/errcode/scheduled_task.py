"""Scheduled task errors, module code 119 (F077).

Registered in the v2.7.0 release contract's module table (11901–11916). One code
per rejection the caller can act on — the task editor, the trigger caller and
the operator each fix a different thing, and merging them would make the failure
copy useless (the F076 convention: every rejection gets its own code).

Two numbers of the registered range are deliberately NOT classes here:

- **11901** (task missing / not visible / cross-tenant) is answered by the
  existing ``NotFoundError`` on the management side and by 11809/404 on the
  trigger side — both already collapse "does not exist" and "may not see it"
  on purpose (anti-enumeration, spec AC-33).
- **11906** (creator lacks ``edit_app``) is the platform's standing 403 reply
  (``UnAuthorizedError``), matching F057/F062 and spec AC-25's "一律 403".

Wire format (design §3 决策 15): on the key-authenticated ``/api/v2`` face these
errors answer with their **real HTTP status** (marked by ``open_api_call`` in the
request scope); on the ``/api/v1`` management face they keep the platform's
legacy envelope (HTTP 200 + business code in the body) so the console's existing
interceptors behave exactly as before.
"""

from .base import BaseErrorCode


class ScheduledTaskErrorBase(BaseErrorCode):
    """Common base carrying the real HTTP status used on the ``/api/v2`` face.

    Starlette matches exception handlers by MRO, so registering this base wires
    every subclass below (same pattern as F076's ``OpenApiErrorBase``).
    """

    HttpStatus: int = 400


class ScheduledTaskConfigInvalidError(ScheduledTaskErrorBase):
    """The task's configuration breaks a rule (spec AC-01, AC-44).

    Covers: a required field missing, an app type that cannot be scheduled, and
    preset inputs that are malformed or over the size/count caps. One code
    because the caller fixes them all in the same editor; the copy names the
    offending field.
    """

    Code: int = 11902
    Msg: str = "Invalid scheduled task configuration"


class ScheduledTaskCronInvalidError(ScheduledTaskErrorBase):
    """The cron expression was rejected by the single parser (spec AC-06).

    The copy must say which shape is wrong (not five fields / out-of-range
    value / unsupported alias) — "invalid expression" leaves the admin guessing.
    """

    Code: int = 11903
    Msg: str = "Invalid schedule expression"


class ScheduledTaskLimitExceededError(ScheduledTaskErrorBase):
    """The app already has the maximum number of enabled tasks (spec AC-05)."""

    Code: int = 11904
    Msg: str = "Too many scheduled tasks for this app"


class ScheduledTaskIdentityUnavailableError(ScheduledTaskErrorBase):
    """The locked run identity cannot be used (spec AC-20, AC-22).

    One of: the service account is gone, disabled, its owner was replaced or
    promoted, or the owner no longer holds ``use_app`` on the target app. The
    trigger-side answer is a real 409 so an external caller (and the scheduler's
    own bookkeeping) can tell it apart from "not found" and "not allowed".
    """

    Code: int = 11905
    Msg: str = "Run identity unavailable"
    HttpStatus: int = 409


class ScheduledTaskAppUnavailableError(ScheduledTaskErrorBase):
    """The target app is offline or deleted (spec AC-16, AC-31).

    Scheduled rounds *skip* an offline app rather than failing (the row records
    ``skipped``); this code is what the synchronous entry points — event trigger
    and "run now" — answer with, so a human or an external system gets an
    immediate, distinguishable rejection instead of silence.
    """

    Code: int = 11907
    Msg: str = "Target app unavailable"
    HttpStatus: int = 409


class ScheduledTaskAppInUseError(ScheduledTaskErrorBase):
    """The app still has enabled scheduled tasks and cannot be deleted (spec AC-30).

    The message must carry the count and point at the next step (stop or delete
    the tasks first) — being blocked without a way forward is what the datasource
    delete guard already avoids.
    """

    Code: int = 11908
    Msg: str = "App has enabled scheduled tasks"


class ScheduledTaskRunRejectedError(ScheduledTaskErrorBase):
    """ "Run now" was refused because a round is running or the platform is full.

    Spec AC-17: a human-triggered action gets an immediate answer — the request
    is not queued and not deferred, unlike a scheduled round which waits for the
    next tick.
    """

    Code: int = 11909
    Msg: str = "Run rejected: task is running or platform is at capacity"
    HttpStatus: int = 409


class ScheduledTaskAlreadyRunningError(ScheduledTaskErrorBase):
    """An event trigger arrived while this task already has a live run (spec AC-38).

    Refusal rather than idempotent acceptance: silent deduplication would make
    the caller believe a second run was accepted.
    """

    Code: int = 11910
    Msg: str = "Task already has a run in progress"
    HttpStatus: int = 409


class ScheduledTaskTriggerRateLimitedError(ScheduledTaskErrorBase):
    """The per-task trigger-frequency guardrail tripped (spec AC-38).

    This is the scheduler's amplification guard (default 10/minute), distinct
    from the platform's API rate limiting, which F077 does not implement.
    ``Retry-After`` accompanies the 429.
    """

    Code: int = 11911
    Msg: str = "Trigger rate limit exceeded"
    HttpStatus: int = 429


class ScheduledTaskReplayRejectedError(ScheduledTaskErrorBase):
    """The trigger request failed replay protection (spec AC-37).

    Missing/out-of-window timestamp or an already-used nonce. The copy should
    say which of the two, because the fix differs (clock skew vs. regenerate).
    """

    Code: int = 11912
    Msg: str = "Trigger request rejected (stale timestamp or reused nonce)"


class ScheduledTaskKnowledgeUnauthorizedError(ScheduledTaskErrorBase):
    """Creation-time check: the run identity cannot read bound knowledge (spec AC-29).

    Raised only when the app judges knowledge visibility by the *run identity*
    (the per-app switch is on). The message carries the offending scopes so the
    admin knows exactly which grants to add. The runtime counterpart never falls
    back to the configurer's visibility — it just returns empty results.
    """

    Code: int = 11913
    Msg: str = "Run identity lacks read access to bound knowledge"


class ScheduledTaskAppNotSchedulableError(ScheduledTaskErrorBase):
    """The app cannot be put on a schedule at all (spec AC-47).

    Currently: more than one human-input node, or a file-type input — an
    unattended run has no one to answer those, and a partially-submitted run
    would report success while having skipped work.
    """

    Code: int = 11914
    Msg: str = "App cannot be scheduled (multiple inputs or file inputs)"


class ScheduledTaskNlDraftInvalidError(ScheduledTaskErrorBase):
    """The natural-language draft was rejected and nothing was stored (spec AC-41).

    Model output is untrusted input; the server re-validates the generated
    schedule, target app and inputs. A rejected draft is not a saved task.
    """

    Code: int = 11915
    Msg: str = "Natural-language draft rejected"


class ScheduledTaskTriggerPayloadInvalidError(ScheduledTaskErrorBase):
    """The trigger body carried fields outside the whitelist (spec AC-35).

    Only ``task_id`` and an optional ``inputs`` mapping are accepted. A raw
    ``user_id`` is answered by F076's own guard (400/11817) before this is
    reached; this code covers everything else (e.g. an ``app_id`` or a
    hand-written schedule) so a caller can tell a payload mistake from a replay
    rejection.
    """

    Code: int = 11916
    Msg: str = "Trigger request payload not allowed"
