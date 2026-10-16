"""F077 scheduled-task configuration (定时/事件触发与定时任务生成).

Platform-level operational knobs only — there is deliberately **no feature-wide
enable switch** (design §3 决策 8/16): every path F077 adds is new (table,
endpoints, beat entry, panel item, audit namespace), so nothing existing needs
to be isolated; an operator who wants to stop scheduling can simply disable the
beat entry.

Kept in YAML rather than the DB because every consumer is a beat/worker process
that reads its config at startup, and because the DB config layer caches for
~100s — a knob an operator flips in an emergency must not sit behind that.
"""

from pydantic import BaseModel, Field


class ScheduledTaskConf(BaseModel):
    """Scheduled-task runtime knobs (all overridable from ``config.yaml``)."""

    global_max_running: int = Field(
        default=10,
        description=(
            "Platform-wide cap on concurrently running scheduled tasks (spec AC-12). "
            "Protects the shared workflow_celery thread pool — one DAG occupies a "
            "worker thread for its whole run. Rounds beyond the cap wait for the "
            "next tick instead of queueing."
        ),
    )
    max_tasks_per_app: int = Field(
        default=20,
        description="Enabled tasks allowed per application (spec AC-05); creation is refused beyond it.",
    )
    run_timeout_seconds: int = Field(
        default=3600,
        description=(
            "Run lease length, in seconds (spec AC-15). Stored as an absolute deadline "
            "on the row, so changing this only affects future leases. There is no "
            "platform-wide run timeout to fall back on — the lease IS the watchdog."
        ),
    )
    consecutive_failure_threshold: int = Field(
        default=5,
        description="Consecutive failures before a task is auto-disabled (spec AC-55).",
    )
    replay_window_seconds: int = Field(
        default=300,
        description=(
            "Accepted |now - X-Bs-Timestamp| window for event triggers (spec AC-37). "
            "The nonce TTL is twice this value so a replay straddling the window edge "
            "is still caught."
        ),
    )
    event_rate_limit_per_minute: int = Field(
        default=10,
        description=(
            "Per-task trigger-frequency guardrail on the event-trigger face (spec AC-38). "
            "This is the scheduler's amplification guard, NOT the platform's API rate "
            "limiting (which F077 does not implement)."
        ),
    )
    preset_input_max_items: int = Field(default=20, description="Preset-input entries allowed per task (spec AC-44).")
    preset_input_max_chars: int = Field(default=2000, description="Character cap per preset-input value (spec AC-44).")
    nl_max_chars: int = Field(default=2000, description="Natural-language description length cap (spec AC-41).")
    preview_count: int = Field(
        default=5,
        description=(
            "How many upcoming fire times the preview returns (spec AC-07). The same "
            "pure function feeds the list's 'next run' column, so the two never drift."
        ),
    )
    dispatch_lock_ttl_seconds: int = Field(
        default=15,
        description=(
            "Platform-wide Redis lock TTL for one dispatch round — kept slightly below "
            "the beat tick so a crashed round never blocks the next one."
        ),
    )
