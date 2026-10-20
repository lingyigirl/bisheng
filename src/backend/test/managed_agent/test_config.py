"""F078 T-05: ManagedAgentConf defaults, YAML override and beat entry."""

from bisheng.core.config.managed_agent import ManagedAgentConf
from bisheng.core.config.settings import CeleryConf, Settings

BEAT_KEY = "managed_agent_health_scan"


def test_defaults_match_design():
    conf = ManagedAgentConf()
    assert conf.enabled is False
    assert conf.connect_timeout_seconds == 10.0
    assert conf.read_timeout_seconds == 60.0
    assert conf.invoke_overall_timeout_seconds == 120.0
    assert conf.max_response_bytes == 1_000_000
    assert conf.rate_limit_per_minute == 60
    assert conf.health_interval_seconds == 300
    assert conf.health_failure_threshold == 3
    assert conf.auto_offline_on_health_failure is False
    assert conf.max_agents_per_tenant == 100
    assert conf.egress_allowlist == []
    assert conf.proxy is None


def test_yaml_section_overrides_defaults():
    settings = Settings(managed_agent={"enabled": True, "rate_limit_per_minute": 5})
    assert settings.managed_agent.enabled is True
    assert settings.managed_agent.rate_limit_per_minute == 5
    assert settings.managed_agent.health_interval_seconds == 300


def test_beat_entry_is_numeric_and_pinned_to_default_queue():
    entry = CeleryConf().beat_schedule[BEAT_KEY]

    assert entry["task"] == "bisheng.worker.managed_agent.health_scan.scan_managed_agents"
    assert isinstance(entry["schedule"], float)
    assert entry["schedule"] == 300.0
    assert entry["options"]["queue"] == "celery"


def test_beat_entry_stays_a_float_after_validate():
    conf = CeleryConf()
    conf.validate()
    assert isinstance(conf.beat_schedule[BEAT_KEY]["schedule"], float)
