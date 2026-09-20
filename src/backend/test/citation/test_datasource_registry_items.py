"""F061 T-31: the citation registry must handle the DATASOURCE type.

Both the write path (`_group_registry_items`) and the read path
(`_load_source_payload`) were written as a RAG/else binary, so a datasource
item fell into the WEB branch and failed validation against
`WebCitationPayloadSchema` (`url Field required`).

That matters well beyond a missing record: `save_citations` runs on the
assistant chat path BEFORE the answer is streamed, and its caller's
`except Exception` answers with a system error and closes the socket. A
datasource-backed answer would have surfaced as an error instead of an answer.
"""

# The Chinese fixture value mirrors a datasource name shown in the UI, as the
# datasource test module does; it carries no ambiguous unicode, so no RUF001
# suppression is needed here.

from bisheng.citation.domain.schemas.citation_schema import (
    CitationRegistryItemSchema,
    CitationType,
)
from bisheng.citation.domain.services.citation_registry_service import (
    CitationRegistryService,
)


def _datasource_item(citation_id='datasource_7_0', statement='SELECT id FROM orders'):
    return CitationRegistryItemSchema(
        citationId=citation_id,
        type=CitationType.DATASOURCE,
        sourcePayload={
            'datasourceId': 7,
            'datasourceName': '客户订单库',
            'tables': ['orders'],
            'statement': statement,
            'columns': ['id'],
            'rows': [[1]],
            'truncated': False,
        },
    )


def test_grouping_a_datasource_item_does_not_raise():
    """It used to fall into the WEB branch and raise ValidationError."""
    grouped = CitationRegistryService._group_registry_items([_datasource_item()])

    assert len(grouped) == 1
    assert grouped[0].type == CitationType.DATASOURCE


def test_a_datasource_item_passes_through_unchanged():
    """One execution == one self-contained anchor. There are no sub-items to
    merge, and re-validating through a RAG/WEB payload would corrupt it."""
    item = _datasource_item()

    grouped = CitationRegistryService._group_registry_items([item])

    assert grouped[0].citationId == item.citationId
    assert grouped[0].sourcePayload.statement == 'SELECT id FROM orders'
    assert grouped[0].sourcePayload.datasourceId == 7
    assert grouped[0].sourcePayload.rows == [[1]]


def test_two_executions_stay_two_anchors():
    """They must not be merged into one the way RAG chunks are."""
    grouped = CitationRegistryService._group_registry_items(
        [_datasource_item('datasource_7_0', 'SELECT 1'), _datasource_item('datasource_7_1', 'SELECT 2')]
    )

    assert [g.citationId for g in grouped] == ['datasource_7_0', 'datasource_7_1']


def test_loading_a_persisted_datasource_payload_validates_against_its_own_schema():
    """The read path used to validate it as a WEB payload."""
    loaded = CitationRegistryService._load_source_payload(
        CitationType.DATASOURCE.value,
        {'datasourceId': 7, 'datasourceName': '客户订单库', 'statement': 'SELECT 1', 'tables': ['orders']},
    )

    assert loaded['datasourceId'] == 7
    assert loaded['statement'] == 'SELECT 1'


def test_rag_and_web_grouping_are_unchanged():
    """The fix must add a branch, not reorder the existing two."""
    from bisheng.citation.domain.schemas.citation_schema import RagCitationPayloadSchema

    rag = CitationRegistryItemSchema(
        citationId='rag1',
        type=CitationType.RAG,
        sourcePayload=RagCitationPayloadSchema(documentName='doc.txt', items=[]),
    )

    grouped = CitationRegistryService._group_registry_items([rag])

    assert grouped[0].type == CitationType.RAG
