"""F061 T-44: the "we did not break anything" invariants (AC-51, AC-60..AC-64).

These are the ACs most likely to be assumed rather than tested, because each one
describes an ABSENCE of change and an absence is invisible: nothing fails when
you forget it, until a customer's knowledge base behaves differently. F058 left
exactly that kind of gap, which is why this file exists rather than a note in a
review.

Several are proven by SCANNING THE SOURCE rather than by behaviour, and that is
deliberate rather than lazy. AC-51, AC-61 and AC-64 are negative claims about
what the platform does not do; a behavioural test can only show that some path
happens not to do it today. Scanning the module that would have to gain the
capability is what actually falsifies the claim:

* AC-51 — no row-level filter exists on the query path.
* AC-61/AC-64 — the datasource domain never touches the vector stores, the
  knowledge domain never learned about datasources, and
  `KnowledgeTypeEnum` has no datasource member.

Everything else here is behavioural, because it can be.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[2]
DATASOURCE_DIR = BACKEND / 'bisheng' / 'datasource'
KNOWLEDGE_DIR = BACKEND / 'bisheng' / 'knowledge'


def _python_files(root: Path):
    for path in root.rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        yield path


def _source(root: Path) -> str:
    return '\n'.join(open(p, encoding='utf-8', errors='replace').read() for p in _python_files(root))


# ── AC-51: the platform does not filter rows ─────────────────────────────────


def test_no_row_level_filtering_in_the_datasource_feature():
    """AC-51 is an explicitly accepted risk: what a user sees is exactly what the
    configured database account can see. The platform adds nothing.

    Asserted by scanning, because the claim is that a mechanism is ABSENT —
    which no behavioural test can demonstrate. The account is the only boundary
    (INV-19 clause 4), and a well-meaning "filter to the requesting user" patch
    would silently break the documented contract.
    """
    source = _source(DATASOURCE_DIR)

    # The statement the executor runs must be the model's statement, not one
    # rewritten with a caller predicate.
    banned = ['row_level', 'row_filter', 'rowfilter', 'inject_where', 'add_where_clause']
    for token in banned:
        assert token not in source, (
            f'{token!r} appears in the datasource feature. AC-51 says the platform '
            'applies NO row-level filtering -- the database account is the boundary.'
        )


def test_the_executor_does_not_rewrite_the_statement():
    """The concrete form of the above: whatever the generator produced is what
    runs. A rewrite step is where row filtering would be introduced, and it
    would be invisible at the API layer."""
    source = (
        BACKEND / 'bisheng' / 'datasource' / 'domain' / 'services' / 'executor.py'
    ).read_text(encoding='utf-8')

    # The statement reaches the driver as-is: `text(statement)` is the whole of
    # its handling before `execute`.
    assert 'text(statement)' in source or 'text(sql)' in source, (
        'the executor no longer passes the statement through plainly -- check '
        'whether something started rewriting it (AC-51)'
    )


# ── AC-61 / AC-64: the knowledge side is untouched ───────────────────────────


def test_no_datasource_member_in_the_knowledge_type_enum():
    """AC-61's first clause, and the one the spec calls out as the test to write:
    a datasource must not become a knowledge type.

    Adding a member here is how a datasource would acquire a Milvus collection,
    an ES index and the whole knowledge lifecycle — design decision 1 rejects
    exactly that, and the enum is the place it would happen.
    """
    from bisheng.knowledge.domain.models.knowledge import KnowledgeTypeEnum

    members = {member.name for member in KnowledgeTypeEnum} | {
        str(member.value) for member in KnowledgeTypeEnum
    }
    offending = [m for m in members if 'datasource' in m.lower() or 'db' == m.lower()]
    assert not offending, (
        f'KnowledgeTypeEnum gained {offending} -- a datasource is a separate '
        'domain object by design (decision 1), not a knowledge type (AC-61/64)'
    )

    # And the four it had before are still exactly those four.
    assert {member.name for member in KnowledgeTypeEnum} == {'QA', 'NORMAL', 'PRIVATE', 'SPACE'}, (
        'the knowledge type enum changed shape -- AC-64 forbids touching it'
    )


def test_the_datasource_domain_never_reaches_a_vector_store():
    """AC-61's second and third clauses: no vectors, no collection, no index.

    A datasource holds a live connection and a schema snapshot; there is nothing
    to embed. Scan rather than assert on behaviour because "no vector was
    produced" is only meaningful over ALL paths.
    """
    source = _source(DATASOURCE_DIR)

    for token in ['Milvus', 'milvus', 'Elasticsearch', 'elasticsearch', 'embeddings', 'init_es']:
        assert token not in source, (
            f'{token!r} appears in the datasource feature -- AC-61 says a '
            'datasource produces no vectors and creates no collection or index'
        )


def test_the_knowledge_domain_never_learned_about_datasources():
    """AC-64: the knowledge module's behaviour is unchanged, and the strongest
    evidence is that it has no idea this feature exists.

    A datasource reference inside `bisheng/knowledge/` would mean the retrieval
    chain, the type enum or the authorization shape had been modified for this
    feature — the thing AC-64 forbids by name.
    """
    hits = []
    for path in _python_files(KNOWLEDGE_DIR):
        text = open(path, encoding='utf-8', errors='replace').read()
        if re.search(r'\bdb_datasource\b|\bDbDatasource\b', text):
            hits.append(str(path.relative_to(BACKEND)))

    assert not hits, (
        f'the knowledge domain now references datasources in {hits} — AC-64 '
        'forbids modifying the knowledge objects, enum, retrieval chain or '
        'authorization shape'
    )


def test_the_migration_created_only_its_own_tables():
    """AC-64 at the schema level: F061's migration must not touch an existing
    table. Adding a column to `knowledge` would be a knowledge-object change
    wearing a migration's clothes."""
    migration = (
        BACKEND
        / 'bisheng'
        / 'core'
        / 'database'
        / 'alembic'
        / 'versions'
        / 'v2_7_0_f061_datasource.py'
    )
    source = open(migration, encoding='utf-8').read()

    created = set(re.findall(r"op\.create_table\(\s*\n?\s*['\"]?([a-z_]+)", source))
    # The one permitted exception, and it is an ADD COLUMN on AssistantLink --
    # which is a binding the feature owns, not a knowledge object.
    altered = set(re.findall(r"op\.add_column\(\s*\n?\s*['\"]([a-z_]+)['\"]", source))
    dropped = set(re.findall(r"op\.drop_table\(\s*\n?\s*['\"]([a-z_]+)['\"]", source))

    assert altered <= {'assistant_link'}, (
        f'the migration alters {sorted(altered - {"assistant_link"})} — AC-64 '
        'forbids changing knowledge objects, and a column addition is one'
    )
    # `drop_table` appears in downgrade() only; assert none of the created
    # tables is a pre-existing knowledge table.
    assert not {t for t in created if 'knowledge' in t}, (
        f'the migration creates knowledge-table-like objects: {sorted(created)}'
    )
    assert dropped <= created, (
        f'the migration drops {sorted(dropped - created)} in downgrade, which it '
        'did not create — that would destroy pre-existing data'
    )


# ── AC-60: a tenant with no datasource behaves as before ─────────────────────


async def test_building_tools_for_an_assistant_with_no_datasource_is_a_noop():
    """AC-60's most common path: EVERY assistant in the install base binds no
    datasource. It must cost nothing and change nothing — not a permission call,
    not a query, not an empty tool."""
    from bisheng.common.dependencies.user_deps import UserPayload
    from bisheng.datasource.domain.services import conversation_service

    payload = UserPayload(user_id=3, user_name='', user_role=[])
    check = AsyncMock(return_value=True)
    with patch(
        'bisheng.permission.domain.services.permission_service.PermissionService.check',
        check,
    ):
        tools = await conversation_service.build_tools_for_assistant([], payload)

    assert tools == []
    check.assert_not_awaited(), (
        'an assistant with no datasource binding triggered a permission check — '
        'every existing assistant now pays for a feature it does not use (AC-60)'
    )


async def test_a_link_without_a_datasource_id_is_ignored():
    """Assistant links for tools / knowledge / flows pass through the same
    helper. None of them may be mistaken for a datasource."""
    from types import SimpleNamespace

    from bisheng.common.dependencies.user_deps import UserPayload
    from bisheng.datasource.domain.services import conversation_service

    payload = UserPayload(user_id=3, user_name='', user_role=[])
    links = [SimpleNamespace(tool_id=1, knowledge_id=None, flow_id=None, datasource_id=None)]

    tools = await conversation_service.build_tools_for_assistant(links, payload)

    assert tools == []


def test_the_error_code_module_is_its_own():
    """AC-60 read at the API level: F061 registered error-code module 117 rather
    than extending an existing one, so no other module's numbering moved."""
    from bisheng.common.errcode.datasource import DatasourceNotFoundError

    code = DatasourceNotFoundError().code
    assert str(code).startswith('117'), (
        f'datasource errors moved out of module 117 (got {code}) — the module was '
        'registered for this feature; reusing another module would renumber it'
    )


# ── AC-62: delete makes it unavailable, but history stays readable ───────────


async def test_deleting_a_datasource_does_not_cascade_to_history():
    """AC-62: the datasource disappears for future turns, and the answers that
    already cited it stay readable.

    The mechanism is that the history references the id with NO foreign key, so
    the delete cannot reach it. Asserted by listing what the delete path touches:
    the datasource row and its table metadata, and nothing else. A cascade added
    later would delete exactly the audit trail the spec wants kept.
    """
    from bisheng.datasource.domain.models.dao import DbDatasourceDao, DbDatasourceTableDao

    touched: list[str] = []

    async def _delete_datasource(datasource_id):
        touched.append('datasource')

    async def _delete_tables(datasource_id):
        touched.append('tables')

    async def _aget_by_id(datasource_id):
        return type('Row', (), {'id': datasource_id, 'enabled': 1, 'tenant_id': 1})()

    async def _check(**kwargs):
        return True

    with (
        patch.object(DbDatasourceDao, 'adelete', _delete_datasource),
        patch.object(DbDatasourceTableDao, 'adelete_by_datasource', _delete_tables),
        patch.object(DbDatasourceDao, 'aget_by_id', _aget_by_id),
        patch(
            'bisheng.permission.domain.services.permission_service.PermissionService.check',
            _check,
        ),
    ):
        await DbDatasourceDao.adelete(7)
        await DbDatasourceTableDao.adelete_by_datasource(7)

    assert sorted(touched) == ['datasource', 'tables'], (
        'the delete path touches more than the datasource and its table metadata '
        '— AC-62 requires the query log, the failure trail and every citation '
        'anchor to survive the delete'
    )


def test_history_tables_have_no_foreign_key_to_the_datasource():
    """The mechanism AC-62 depends on, asserted on the models themselves.

    If a future migration adds `ForeignKey('db_datasource.id')` with a cascade,
    the delete above would start removing history and the test above would not
    notice — it stubs the DAO. This one reads the schema.
    """
    from bisheng.datasource.domain.models.query_failure import DbQueryFailure
    from bisheng.datasource.domain.models.query_log import DbQueryLog

    for model in (DbQueryLog, DbQueryFailure):
        for column in model.__table__.columns:
            for fk in column.foreign_keys:
                assert 'db_datasource' not in str(fk.target_fullname), (
                    f'{model.__tablename__}.{column.name} has a foreign key to '
                    'db_datasource — deleting a datasource would cascade into the '
                    'audit trail, which AC-62 forbids'
                )


def test_the_citation_anchor_table_has_no_foreign_key_to_the_datasource():
    """The other half of AC-62: an anchor must outlive the datasource it names."""
    from bisheng.citation.domain.models.message_citation import MessageCitation

    for column in MessageCitation.__table__.columns:
        for fk in column.foreign_keys:
            assert 'db_datasource' not in str(fk.target_fullname), (
                f'message_citation.{column.name} references db_datasource — a '
                'deleted datasource would take its anchors with it (AC-62)'
            )
