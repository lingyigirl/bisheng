"""Guard: F078's three tables must stay in the tenant-aware module list.

The discovery snapshot runs once at startup. An unregistered table silently
reads other tenants' rows and misses the ``before_flush`` tenant_id fill, so a
child tenant's agent/trace rows would land in ROOT. The list is made of strings,
so nothing about the models keeps them registered (same class of guard as
``test/datasource/test_tenant_registration.py``).
"""

import pytest

from bisheng.core.database.tenant_filter import _TENANT_AWARE_MODEL_MODULES

F078_MODULES = (
    "bisheng.managed_agent.domain.models.managed_agent",
    "bisheng.managed_agent.domain.models.taxonomy",
    "bisheng.managed_agent.domain.models.run",
)


@pytest.mark.parametrize("module_path", F078_MODULES)
def test_f078_table_is_registered_as_tenant_aware(module_path):
    assert module_path in _TENANT_AWARE_MODEL_MODULES, (
        f"{module_path} is missing from _TENANT_AWARE_MODEL_MODULES. Without it "
        "the table is neither tenant-filtered on read nor auto-filled on insert."
    )


@pytest.mark.parametrize("module_path", F078_MODULES)
def test_registered_module_path_actually_imports(module_path):
    __import__(module_path)


def test_every_f078_model_declares_a_tenant_id_column():
    from bisheng.managed_agent.domain.models.managed_agent import ManagedAgent
    from bisheng.managed_agent.domain.models.run import ManagedAgentRun
    from bisheng.managed_agent.domain.models.taxonomy import ManagedAgentTaxonomy

    for model in (ManagedAgent, ManagedAgentTaxonomy, ManagedAgentRun):
        assert "tenant_id" in model.__table__.columns, (
            f"{model.__tablename__} has no tenant_id column but is registered as tenant-aware"
        )
