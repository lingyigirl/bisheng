"""The preset extraction templates seeded at startup (F058).

Modelled on ``test/approval/test_init_default_approval_scenarios.py`` -- same
in-memory sqlite harness, and the same two things to prove: the rows land for
the default tenant, and re-running init neither duplicates nor overwrites them.

The last two cases are about the SEED DATA rather than the seeding, and they
matter because the seed writes rows with ``session.add`` directly: going through
the template API would reject a render form that names an undefined field
(AC-03) at save time, and this path skips that validation entirely.
"""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.common.init_data import (
    _DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS,
    _init_default_doc_extract_templates,
)
from bisheng.core.context.tenant import DEFAULT_TENANT_ID
from bisheng.doc_extract.domain.models.extract_template import DocExtractTemplate

# Mirrors ``renderer._PLACEHOLDER_RE``. Deliberately duplicated rather than
# imported: if that regex changes, this should fail to agree rather than quietly
# become a weaker check.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FIELD_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FIELD_TYPES = {"string", "number", "date", "boolean"}

_IDS = [seed["doc_type"] for seed in _DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS]


@pytest_asyncio.fixture
async def template_db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SQLModel.metadata.create_all(
                sync_conn, tables=[DocExtractTemplate.__table__]
            )
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(template_db_engine):
    async with AsyncSession(bind=template_db_engine, expire_on_commit=False) as s:
        yield s


async def test_seeds_the_presets_for_the_default_tenant(session):
    await _init_default_doc_extract_templates(session)

    rows = (await session.exec(select(DocExtractTemplate))).all()
    assert {row.doc_type for row in rows} == set(_IDS)

    for row in rows:
        assert row.tenant_id == DEFAULT_TENANT_ID
        assert row.enabled == 1
        assert row.is_deleted == 0
        # A template with no fields would extract nothing and still classify.
        assert row.field_schema, row.doc_type
        assert row.render_template, row.doc_type


async def test_seeding_is_idempotent(session):
    await _init_default_doc_extract_templates(session)
    await _init_default_doc_extract_templates(session)

    rows = (await session.exec(select(DocExtractTemplate))).all()
    assert len(rows) == len(_DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS)


async def test_an_operator_deletion_survives_a_restart(session):
    """The seed matches soft-deleted rows too, so a delete sticks.

    Matching live rows only would re-create the row on the next startup, which
    makes a preset the operator does not want impossible to remove.
    """
    await _init_default_doc_extract_templates(session)
    target = _DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS[0]["doc_type"]
    row = (
        await session.exec(select(DocExtractTemplate).where(DocExtractTemplate.doc_type == target))
    ).first()
    row.is_deleted = 1
    session.add(row)
    await session.commit()

    await _init_default_doc_extract_templates(session)

    rows = (await session.exec(select(DocExtractTemplate))).all()
    assert len(rows) == len(_DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS)
    revived = (
        await session.exec(select(DocExtractTemplate).where(DocExtractTemplate.doc_type == target))
    ).first()
    assert revived.is_deleted == 1


@pytest.mark.parametrize("seed", _DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS, ids=_IDS)
def test_every_render_placeholder_is_a_defined_field(seed):
    """Spec AC-03. Checked here because the seed bypasses the API's own check."""
    keys = {field["key"] for field in seed["field_schema"]}
    referenced = set(_PLACEHOLDER_RE.findall(seed["render_template"]))
    assert referenced <= keys, (
        f"{seed['doc_type']}: render form names fields the schema does not define: "
        f"{sorted(referenced - keys)}"
    )


@pytest.mark.parametrize("seed", _DEFAULT_DOC_EXTRACT_TEMPLATE_SEEDS, ids=_IDS)
def test_every_field_is_well_formed(seed):
    keys = [field["key"] for field in seed["field_schema"]]
    assert len(keys) == len(set(keys)), f"{seed['doc_type']}: duplicate field keys"

    for field in seed["field_schema"]:
        assert _FIELD_KEY_RE.match(field["key"]), field["key"]
        assert field["type"] in _FIELD_TYPES, field
        assert field["label"], field
        assert field["label"] != field["key"], f"{field['key']}: label is just the key again"

    # At least one required field, or a document missing everything still passes.
    assert any(field.get("required") for field in seed["field_schema"]), seed["doc_type"]

    # And the classifier needs something to tell these apart from other docs.
    assert seed["description"].strip(), seed["doc_type"]
