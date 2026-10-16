"""Queue-namespace resolution for the Linsight task queue.

``linsight_conf.queue_namespace`` exists so a developer pointed at a shared Redis
can take a private queue (``<namespace>:queue``). Its acceptance criterion is
that an unset value changes nothing — these tests pin that down.
"""

from types import SimpleNamespace

import pytest

from bisheng.core.config.settings import LinsightConf
from bisheng.linsight import worker as linsight_worker
from bisheng.linsight.worker import build_linsight_queue, resolve_queue_namespace


@pytest.fixture
def patch_queue_namespace(monkeypatch: pytest.MonkeyPatch):
    """Replace the module-level settings with just the field under test."""

    def _apply(namespace: str) -> None:
        monkeypatch.setattr(
            linsight_worker,
            "settings",
            SimpleNamespace(linsight_conf=SimpleNamespace(queue_namespace=namespace)),
        )

    return _apply


def test_schema_default_is_linsight():
    """The config field defaults to the historical namespace."""
    assert LinsightConf().queue_namespace == "linsight"


def test_resolves_to_linsight_when_unset(patch_queue_namespace):
    """An unset (or empty) value keeps the pre-existing key, so nothing moves."""
    patch_queue_namespace("")
    assert resolve_queue_namespace() == "linsight"


def test_resolves_to_linsight_when_configured_as_linsight(patch_queue_namespace):
    patch_queue_namespace("linsight")
    assert resolve_queue_namespace() == "linsight"


def test_configured_namespace_prefixes_the_queue_key(patch_queue_namespace):
    """LinsightQueue.__init__ only assembles the key string — no Redis needed."""
    patch_queue_namespace("linsight-local")
    queue = build_linsight_queue(object())
    assert queue.key == "linsight-local:queue"
