"""Regression tests for reading the `doc_extract_llm` slot value.

These exist because the original implementation passed every other test in this
package while being completely broken: it did `int(value)` on a value the
database stores as **JSON text**, so the slot always looked unconfigured and
every extraction failed with 11608 no matter what the operator configured.

It escaped the rest of the suite because those tests patch `_resolve_model_id`
to return an integer directly -- the parsing was never executed. The cases below
go through the parser itself, with the value shapes the platform actually
stores (see `llm/domain/services/llm.py`, which does `json.loads(value)` before
building its config object).
"""

import json

import pytest

from bisheng.doc_extract.domain.services.extraction_service import ExtractionService


@pytest.mark.parametrize('stored,expected', [
    # The canonical stored shape -- every platform writer json.dumps a payload.
    (json.dumps({'model_id': 5}), 5),
    # Extra keys are normal (other slots carry tuning alongside the id).
    (json.dumps({'model_id': 3, 'temperature': 0}), 3),
    # Alternate key, tolerated for hand-written rows.
    (json.dumps({'extract_model_id': 9}), 9),
    # Bare numbers, in case a row was written by hand.
    ('3', 3),
    (3, 3),
])
def test_parses_configured_slot(stored, expected):
    assert ExtractionService._parse_slot_value(stored) == expected


@pytest.mark.parametrize('stored', [
    None,
    '',
    json.dumps({}),
    json.dumps({'model_id': None}),
    json.dumps({'model_id': 'abc'}),
    'not json at all',
    [],
])
def test_unconfigured_or_unreadable_yields_none(stored):
    """Unreadable must degrade to None (→ 11608), never raise: this runs on the
    request path before any parsing, so an exception here would be a 500."""
    assert ExtractionService._parse_slot_value(stored) is None


def test_json_object_is_not_mistaken_for_a_number():
    """The exact bug: `int('{"model_id": 5}')` raises, so a dict-shaped value
    silently became "unconfigured"."""
    assert ExtractionService._parse_slot_value(json.dumps({'model_id': 5})) is not None


def test_resolve_model_id_uses_the_parsed_value():
    """End of the wire: a JSON payload from the DAO must yield the model id."""
    from unittest.mock import patch

    with patch(
        'bisheng.llm.domain.models.tenant_system_model_config.'
        'TenantSystemModelConfigDao.resolve',
        return_value=(json.dumps({'model_id': 7}), False, False),
    ):
        assert ExtractionService._resolve_model_id(1) == 7


def test_resolve_model_id_passes_tenant_through():
    """The tenant must reach the DAO. Passing None instead queries
    `tenant_id IS NULL`, which never matches, so a tenant's own slot would be
    unreachable and every call would fall back to Root."""
    from unittest.mock import patch

    with patch(
        'bisheng.llm.domain.models.tenant_system_model_config.'
        'TenantSystemModelConfigDao.resolve',
        return_value=(None, False, False),
    ) as resolve:
        ExtractionService._resolve_model_id(42)

    assert resolve.call_args.args[0] == 42, 'tenant_id must be the first argument'
