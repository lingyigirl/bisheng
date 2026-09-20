"""A datasource citation must be filtered like RAG, not passed like a web link.

This file exists because of what the citation module's type checks looked like
before `CitationType.DATASOURCE` existed. Both dispatchers were written as
"if RAG ... else ...", and the `else` meant WEB -- i.e. "anonymous content,
nothing to check". Adding a new type without touching them would have routed
datasource anchors, which carry table names and the SQL that ran, straight
through unfiltered to any viewer.

So the tests below are about the FALLTHROUGH as much as about the new type.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bisheng.citation.domain.schemas.citation_schema import (
    AUTHORIZED_TYPES,
    CitationRegistryItemSchema,
    CitationType,
)


def _datasource_item(datasource_id=7, **payload_overrides):
    payload = {
        'datasourceId': datasource_id,
        'datasourceName': '客户订单库',
        'tables': ['orders'],
        'statement': 'SELECT id FROM orders',
        'columns': ['id'],
        'rows': [[1]],
        'truncated': False,
    }
    payload.update(payload_overrides)
    return CitationRegistryItemSchema(
        citationId=f'ds-{datasource_id}',
        type=CitationType.DATASOURCE,
        sourcePayload=payload,
    )



def _patch_permission_mock(mock):
    """Context manager that swaps in a mock for the CLASSMETHOD `check`.

    `patch(path, AsyncMock(...))` does not work here: `check` is a classmethod,
    so the attribute is a descriptor and the plain mock is never consulted --
    the real permission service runs instead, which is how these tests first
    passed while reporting `await_count == 0`.
    """
    from bisheng.permission.domain.services.permission_service import PermissionService

    return patch.object(PermissionService, 'check', new=mock)


def _web_item():
    return CitationRegistryItemSchema(
        citationId='web-1',
        type=CitationType.WEB,
        sourcePayload={'url': 'https://example.com', 'title': 'x'},
    )


def _service():
    from bisheng.citation.domain.services.citation_resolve_service import CitationResolveService

    return CitationResolveService(repository=MagicMock())


# --- the type is on the authorized side --------------------------------------


def test_datasource_is_an_authorized_type():
    """The whole fix in one assertion: this type needs filtering.

    If it were absent from this set, every "is this authorized?" check in the
    resolve path would treat an anchor as anonymous content.
    """
    assert CitationType.DATASOURCE in AUTHORIZED_TYPES
    assert CitationType.RAG in AUTHORIZED_TYPES
    assert CitationType.WEB not in AUTHORIZED_TYPES


# --- the visibility filter ----------------------------------------------------


async def test_an_unauthorized_datasource_citation_is_dropped():
    allowed = AsyncMock(return_value=False)
    with _patch_permission_mock(allowed):
        kept = await _service()._drop_invisible_datasource_items(
            [_datasource_item(7)], MagicMock(user_id=3)
        )

    assert kept == [], 'a datasource citation survived without permission'


async def test_an_authorized_datasource_citation_is_kept():
    with _patch_permission_mock(AsyncMock(return_value=True)):
        kept = await _service()._drop_invisible_datasource_items(
            [_datasource_item(7)], MagicMock(user_id=3)
        )

    assert len(kept) == 1


async def test_the_check_uses_the_use_relation_on_the_datasource():
    """Not `can_read` on something else -- the conversation requires the
    datasource's own use relation, and the object type must be `db_datasource`
    or the check would run against the wrong resource family."""
    check = AsyncMock(return_value=True)
    with _patch_permission_mock(check):
        await _service()._drop_invisible_datasource_items([_datasource_item(7)], MagicMock(user_id=3))

    kwargs = check.await_args.kwargs
    assert kwargs['object_type'] == 'db_datasource'
    assert kwargs['object_id'] == '7'
    assert kwargs['relation'] == 'viewer'


async def test_an_anonymous_viewer_gets_no_datasource_citations():
    """Unlike RAG, where anonymous means "no filter", there is no anonymous
    caller who should see a database's structure."""
    kept = await _service()._drop_invisible_datasource_items([_datasource_item(7)], None)

    assert kept == []


async def test_a_permission_error_drops_rather_than_keeps():
    """Fail closed. A check that could not run is not a yes."""
    with _patch_permission_mock(AsyncMock(side_effect=RuntimeError('openfga is down'))):
        kept = await _service()._drop_invisible_datasource_items(
            [_datasource_item(7)], MagicMock(user_id=3)
        )

    assert kept == []


async def test_an_unreadable_payload_is_dropped():
    """A payload that cannot be attributed to a datasource cannot be
    authorized, so it must not be shown."""
    broken = CitationRegistryItemSchema(
        citationId='ds-broken', type=CitationType.DATASOURCE, sourcePayload={'not': 'an anchor'}
    )
    with _patch_permission_mock(AsyncMock(return_value=True)):
        kept = await _service()._drop_invisible_datasource_items([broken], MagicMock(user_id=3))

    assert kept == []


async def test_other_types_are_untouched():
    """The filter must not become a filter for everything."""
    with _patch_permission_mock(AsyncMock(return_value=True)):
        kept = await _service()._drop_invisible_datasource_items(
            [_web_item(), _datasource_item(7)], MagicMock(user_id=3)
        )

    assert len(kept) == 2
    assert {i.type for i in kept} == {CitationType.WEB, CitationType.DATASOURCE}


async def test_the_permission_check_is_cached_within_one_resolve():
    """The same datasource cited twice must not cost two OpenFGA round trips."""
    check = AsyncMock(return_value=True)
    with _patch_permission_mock(check):
        await _service()._drop_invisible_datasource_items(
            [_datasource_item(7), _datasource_item(7)], MagicMock(user_id=3)
        )

    assert check.await_count == 1


# --- the enrichment dispatcher ------------------------------------------------


async def test_a_datasource_item_is_not_enriched_as_a_web_item():
    """The fallthrough in `_enrich_item` is `_enrich_web_item`. A datasource
    anchor sent there would be validated against the web payload schema and
    either fail or come back with its fields dropped."""
    import inspect

    from bisheng.citation.domain.services.citation_resolve_service import CitationResolveService

    source = inspect.getsource(CitationResolveService._enrich_item)
    assert 'CitationType.DATASOURCE' in source, (
        'the enrichment dispatcher no longer handles datasource items explicitly '
        '-- they would fall through to the web branch'
    )


async def test_the_enrichment_branch_validates_the_payload():
    """A payload written by an older process must not reach the frontend
    unchecked; this is the datasource counterpart of the web branch's URL
    normalization."""
    service = _service()

    with patch(
        'bisheng.datasource.domain.schemas.citation.DatasourceCitationPayloadSchema.model_validate',
        MagicMock(side_effect=ValueError('bad payload')),
    ) as validate:
        item = _datasource_item(7)
        with pytest.raises(ValueError):
            service._enrich_datasource_item(item)

    validate.assert_called_once()


async def test_the_enrichment_branch_keeps_the_anchor_fields():
    item = _datasource_item(7, statement='SELECT a FROM t', tables=['t'])

    enriched = _service()._enrich_datasource_item(item)

    payload = enriched.sourcePayload
    # A schema instance, not a dict: `CitationSourcePayload` is a validating
    # union, which is what keeps a malformed payload from reaching the frontend.
    assert payload.statement == 'SELECT a FROM t'
    assert payload.tables == ['t']
    assert payload.datasourceName == '客户订单库'


# --- the anchor round-trips ---------------------------------------------------


def test_the_anchor_payload_round_trips_through_json():
    """It is persisted with the message and read back on reload (AC-34), so it
    has to survive json without a custom encoder."""
    import json

    from bisheng.datasource.domain.schemas.citation import DatasourceCitationPayloadSchema

    original = DatasourceCitationPayloadSchema(
        datasourceId=7,
        datasourceName='客户订单库',
        tables=['orders'],
        statement='SELECT id FROM orders',
        columns=['id', 'amount'],
        rows=[[1, '10.50'], [2, None]],
        truncated=True,
    )

    restored = DatasourceCitationPayloadSchema.model_validate(
        json.loads(json.dumps(original.model_dump()))
    )

    assert restored.datasourceId == 7
    assert restored.rows == [[1, '10.50'], [2, None]]
    assert restored.truncated is True


def test_unknown_fields_survive_a_round_trip():
    """During a rolling deploy an older process may hold a payload a newer one
    wrote; dropping unknown keys would silently strip the anchor's content."""
    from bisheng.datasource.domain.schemas.citation import DatasourceCitationPayloadSchema

    payload = DatasourceCitationPayloadSchema.model_validate(
        {'datasourceId': 7, 'datasourceName': 'x', 'futureField': 'keep me'}
    )

    assert payload.model_dump()['futureField'] == 'keep me'


# --- the filter is actually WIRED IN -----------------------------------------


def test_the_filter_is_called_by_the_resolve_flow():
    """Every other test here calls the filter directly, so none of them would
    notice if the resolve flow stopped calling it.

    Measured: deleting the call from `resolve_citations` left this file green.
    A guard that is not invoked is not a guard -- the same shape of gap the
    F056 review found on the chat entry points, where removing the guard
    arguments kept every test passing.
    """
    import inspect

    from bisheng.citation.domain.services.citation_resolve_service import CitationResolveService

    for method_name in ('resolve_citations', '_filter_visible_rag_items'):
        source = inspect.getsource(getattr(CitationResolveService, method_name))
        assert '_drop_invisible_datasource_items' in source, (
            f'{method_name} no longer filters datasource citations -- an anchor '
            'carrying table names and SQL would reach any viewer'
        )


def test_the_fallthrough_branches_are_explicit():
    """Both type dispatchers must name DATASOURCE rather than rely on an `else`.

    Their `else` means WEB, i.e. "anonymous content, nothing to check". An
    explicit branch is what stops a future type from inheriting that meaning by
    accident -- which is exactly how this one would have gone wrong.
    """
    import inspect

    from bisheng.citation.domain.services.citation_resolve_service import CitationResolveService

    for method_name in ('_enrich_item',):
        source = inspect.getsource(getattr(CitationResolveService, method_name))
        assert 'CitationType.DATASOURCE' in source, (
            f'{method_name} dispatches on type without naming datasource explicitly; '
            'the fallthrough is the WEB branch'
        )
