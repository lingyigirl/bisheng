"""F061 T-07: the db_datasource model-upgrade runbook must be safe to re-run.

An ops script earns its safety from three properties, each asserted here
against a fake OpenFGA client rather than by reading the script:

- **idempotent** — a second run sees the type and writes nothing;
- **dry-run by default** — reporting never mutates the store;
- **readable on failure** — an unreachable OpenFGA returns a distinct exit code
  with a message, not a stack trace (operators run this against production).

The fake client is a thin I/O stand-in that reuses the real
``FGAClient.model_type_names``, so the type-detection under test is the shipped
one, not a re-implementation.
"""

from unittest.mock import AsyncMock

import pytest

from bisheng.core.openfga.client import FGAClient
from bisheng.core.openfga.exceptions import FGAConnectionError
from scripts.migrate_fga_add_datasource_type import (
    EXIT_MODEL_ERROR,
    EXIT_OK,
    EXIT_UNREACHABLE,
    NEW_TYPE,
    run,
)

_OLD_TYPES = ["user", "system", "knowledge_library"]
_NEW_TYPES = [*_OLD_TYPES, NEW_TYPE]


class _FakeClient:
    """Stand-in for FGAClient — records writes, no HTTP."""

    model_type_names = staticmethod(FGAClient.model_type_names)

    def __init__(self, *, current_types=(), model_id="model-old", read_error=None):
        self.store_id = "store-1"
        self.model_id = model_id
        self._current_types = list(current_types)
        self._read_error = read_error
        self.written_models: list[dict] = []
        self.write_error = None

    async def read_authorization_model(self, model_id: str) -> dict:
        if self._read_error is not None:
            raise self._read_error
        assert model_id == self.model_id
        return {"type_definitions": [{"type": t} for t in self._current_types]}

    async def write_authorization_model(self, model: dict) -> str:
        if self.write_error is not None:
            raise self.write_error
        self.written_models.append(model)
        return "model-new"


@pytest.fixture(autouse=True)
def _no_app_context(monkeypatch):
    """The script imports the accessor inside run(); patch it at the source."""
    monkeypatch.setattr(
        "bisheng.core.openfga.manager.aget_fga_client",
        AsyncMock(return_value=None),
    )


def _use(monkeypatch, client):
    monkeypatch.setattr(
        "bisheng.core.openfga.manager.aget_fga_client",
        AsyncMock(return_value=client),
    )
    return client


async def test_skips_when_the_model_already_declares_the_type(monkeypatch):
    client = _use(monkeypatch, _FakeClient(current_types=_NEW_TYPES))

    assert await run(apply=True) == EXIT_OK
    assert client.written_models == [], "an idempotent run must not write a model"


async def test_dry_run_reports_without_writing(monkeypatch):
    client = _use(monkeypatch, _FakeClient(current_types=_OLD_TYPES))

    assert await run(apply=False) == EXIT_OK
    assert client.written_models == []


async def test_apply_writes_the_model_containing_the_new_type(monkeypatch):
    client = _use(monkeypatch, _FakeClient(current_types=_OLD_TYPES))

    assert await run(apply=True) == EXIT_OK
    assert len(client.written_models) == 1
    written_types = FGAClient.model_type_names(client.written_models[0])
    assert NEW_TYPE in written_types
    # The whole shipped model is written, not a patch: OpenFGA model versions
    # are immutable and complete.
    assert "knowledge_library" in written_types


async def test_unreachable_openfga_reports_and_does_not_write(monkeypatch):
    client = _use(
        monkeypatch,
        _FakeClient(current_types=_OLD_TYPES, read_error=FGAConnectionError("connection refused")),
    )

    assert await run(apply=True) == EXIT_UNREACHABLE
    assert client.written_models == []


async def test_write_failure_due_to_connectivity_is_reported_as_unreachable(monkeypatch):
    client = _use(monkeypatch, _FakeClient(current_types=_OLD_TYPES))
    client.write_error = FGAConnectionError("connection reset mid-write")

    assert await run(apply=True) == EXIT_UNREACHABLE
    assert client.written_models == []


async def test_missing_client_is_treated_as_unavailable(monkeypatch):
    """OpenFGA disabled in this environment → readable exit code, no crash."""
    monkeypatch.setattr(
        "bisheng.core.openfga.manager.aget_fga_client",
        AsyncMock(return_value=None),
    )

    assert await run(apply=False) == EXIT_UNREACHABLE


async def test_read_error_that_is_not_connectivity_is_a_model_error(monkeypatch):
    from bisheng.core.openfga.exceptions import FGAModelError

    _use(
        monkeypatch,
        _FakeClient(current_types=_OLD_TYPES, read_error=FGAModelError("no such model")),
    )

    assert await run(apply=False) == EXIT_MODEL_ERROR
