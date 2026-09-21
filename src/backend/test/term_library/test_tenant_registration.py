"""F060 T-03: the term library's tenant filter is actually wired up.

Tenant filtering is DISCOVERED, not declared: `_discover_tenant_aware_tables`
walks `SQLModel.metadata` at startup and takes every table that has a
`tenant_id` column. That sounds automatic, and it nearly is — with one gap that
makes this file necessary. Metadata is only populated for model modules that
have been IMPORTED by the time discovery runs, so a brand-new table can carry
`tenant_id` and still end up unfiltered.

The failure mode is the reason this is asserted twice rather than reasoned
about: an unregistered table still reads, still writes, and still passes every
other test in the suite. What changes is that one tenant's terms become visible
to — and injected into the conversations of — every other tenant, with no error
anywhere. F061 recorded the same trap ("易漏，坑 1") and the codebase carries
older scars from it (`knowledgefile`, `flowversion` wrote child-tenant rows into
root).

So: one assertion on the registration list, which is the wiring, and one
behavioural assertion through the filter itself, which is the effect. The first
would pass if someone registered the module but broke the discovery; the second
would pass if the module happened to be imported transitively today and stopped
being tomorrow.
"""

from bisheng.core.database import tenant_filter

MODULE = 'bisheng.term_library.domain.models.term'


def test_the_model_module_is_registered_for_tenant_filtering():
    """Asserted against the STRING CONSTANT, not by reading behaviour.

    `_TENANT_AWARE_MODEL_MODULES` is a tuple of module paths; a refactor that
    severs an indirect import edge is exactly what it exists to survive, and
    reading the code cannot tell you whether the entry is there today.
    """
    assert MODULE in tenant_filter._TENANT_AWARE_MODEL_MODULES, (
        f'{MODULE} is not in _TENANT_AWARE_MODEL_MODULES. Tenant filtering is '
        'discovered from SQLModel metadata at startup, so an unimported module '
        'means term_library_term is read and written WITHOUT a tenant filter -- '
        'one tenant\'s terms become visible to every other tenant, silently.'
    )


def test_discovery_actually_picks_the_table_up():
    """The registration is necessary but not sufficient: it only guarantees the
    import. This asserts the OTHER half — that once imported, discovery sees the
    table — so a name mismatch between the module path and the table would fail
    here rather than in production.
    """
    tenant_filter.register_tenant_filter_events()
    tenant_filter._tenant_aware_tables = tenant_filter._discover_tenant_aware_tables()

    assert 'term_library_term' in tenant_filter._tenant_aware_tables, (
        'the table was not discovered as tenant-aware. Either its tenant_id '
        'column is missing, or the module never imported, or the table name '
        'differs from what discovery sees.'
    )


def test_the_table_really_has_a_tenant_column():
    """The precondition discovery keys on. Stated explicitly because the whole
    mechanism reduces to "does this table have tenant_id" — a rename would take
    the table out of filtering as quietly as a missing registration would.
    """
    from bisheng.term_library.domain.models.term import TermLibraryTerm

    assert 'tenant_id' in TermLibraryTerm.__table__.c, (
        'term_library_term has no tenant_id column, so no tenant filter can be '
        'derived for it at all (contract INV-16)'
    )
