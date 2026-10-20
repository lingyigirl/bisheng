"""Standalone workers must resolve explicit grants through real permission infrastructure."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bisheng.core.context import manager
from bisheng.core.openfga import manager as fga_manager
from bisheng.linsight import worker


async def test_worker_registers_and_initializes_permission_context(monkeypatch):
    config = SimpleNamespace(enabled=True)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(openfga=config))
    context = SimpleNamespace(async_get_instance=AsyncMock())
    registry = SimpleNamespace(get_context=Mock(side_effect=KeyError), register_context=Mock())
    factory = Mock(return_value=context)
    monkeypatch.setattr(manager, "app_context", registry)
    monkeypatch.setattr(fga_manager, "FGAManager", factory)
    await worker.initialize_worker_permissions()
    factory.assert_called_once_with(openfga_config=config)
    registry.register_context.assert_called_once_with(context)
    context.async_get_instance.assert_awaited_once()


async def test_worker_reuses_context_and_does_not_hide_initialization_failure(monkeypatch):
    monkeypatch.setattr(worker, "settings", SimpleNamespace(openfga=SimpleNamespace(enabled=True)))
    context = SimpleNamespace(async_get_instance=AsyncMock(side_effect=RuntimeError("OpenFGA unavailable")))
    registry = SimpleNamespace(get_context=Mock(return_value=context), register_context=Mock())
    monkeypatch.setattr(manager, "app_context", registry)
    with pytest.raises(RuntimeError, match="OpenFGA unavailable"):
        await worker.initialize_worker_permissions()
    registry.register_context.assert_not_called()


async def test_disabled_openfga_does_not_register_context(monkeypatch):
    monkeypatch.setattr(worker, "settings", SimpleNamespace(openfga=SimpleNamespace(enabled=False)))
    registry = Mock()
    monkeypatch.setattr(manager, "app_context", registry)
    await worker.initialize_worker_permissions()
    registry.get_context.assert_not_called()
