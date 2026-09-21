"""F060 T-10: the term library's HTTP surface and its gates.

Spec AC-17 says the backend must reject, and must not leave the decision to a
hidden button in the UI. That is the whole reason these tests call the endpoint
functions directly with a user whose menus say no: the 403 has to be raised by
the server even though the platform page would never have offered the action.

The gate is a single classmethod on `UserPayload`, so the tests patch THAT and
not the endpoint's own helper — patching the helper would assert that the helper
was called, not that it decides anything.

One test is about routing rather than permissions, and it is here because the
bug it prevents is silent: `/import` and `/import/preview` are literal paths
that an unconstrained `/{term_id}` registered first would swallow, answering 200
from the wrong handler instead of 404. F061 shipped exactly that with
`query-failures`.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.term_library.api.endpoints import terms
from bisheng.term_library.domain.services.term_importer import ImportMode


class _User:
    user_id = 3
    tenant_id = 1


def _menu_gate(monkeypatch, granted: set[str]) -> None:
    """Accept only the menu keys in `granted`, reject everything else."""

    async def _assert(user_id, menu_key):
        if menu_key not in granted:
            from bisheng.common.errcode.http_error import UnAuthorizedError

            raise UnAuthorizedError.http_exception()

    monkeypatch.setattr(UserPayload, 'assert_effective_web_menu_contains', _assert)


def _term(term_id=1):
    return SimpleNamespace(
        id=term_id,
        standard_term='不良贷款率',
        definition='不良贷款占贷款总额的比例',
        aliases=['不良率'],
        department_id=None,
        create_user_id=3,
        create_time=None,
        update_time=None,
    )


READ = {'knowledge'}
WRITE = {'knowledge', 'create_knowledge'}


# ── AC-17: the read gate ─────────────────────────────────────────────────────


async def test_listing_requires_the_knowledge_menu(monkeypatch):
    _menu_gate(monkeypatch, granted=set())

    with pytest.raises(HTTPException):
        await terms.list_terms(login_user=_User())


async def test_listing_is_open_to_anyone_with_the_knowledge_menu(monkeypatch):
    """The library has no per-term authorization to check (AC-16/AC-31), so the
    menu key is the entire requirement — a user with it sees the whole list."""
    _menu_gate(monkeypatch, granted=READ)

    async def _list_terms(**_kwargs):
        return [_term()], 1

    monkeypatch.setattr(terms.term_service, 'list_terms', _list_terms)

    response = await terms.list_terms(login_user=_User())

    assert response.data['total'] == 1
    assert response.data['data'][0]['standard_term'] == '不良贷款率'


# ── AC-17: the write gate, on every mutating endpoint ────────────────────────


async def test_creating_is_refused_without_create_knowledge(monkeypatch):
    _menu_gate(monkeypatch, granted=READ)

    with pytest.raises(HTTPException):
        await terms.create_term(terms.TermCreatePayload(standard_term='A', definition='a'), login_user=_User())


async def test_updating_is_refused_without_create_knowledge(monkeypatch):
    _menu_gate(monkeypatch, granted=READ)

    with pytest.raises(HTTPException):
        await terms.update_term(1, terms.TermUpdatePayload(standard_term='A', definition='a'), login_user=_User())


async def test_deleting_is_refused_without_create_knowledge(monkeypatch):
    _menu_gate(monkeypatch, granted=READ)

    with pytest.raises(HTTPException):
        await terms.delete_term(1, login_user=_User())


async def test_importing_is_refused_without_create_knowledge(monkeypatch):
    """The import endpoints write the whole library, so they are the last place
    to forget the gate — and the file has to be rejected BEFORE it is read."""
    _menu_gate(monkeypatch, granted=READ)
    file = SimpleNamespace(read=lambda: _unused())

    with pytest.raises(HTTPException):
        await terms.preview_import(file=file, mode=ImportMode.APPEND, login_user=_User())
    with pytest.raises(HTTPException):
        await terms.execute_import(file=file, mode=ImportMode.APPEND, login_user=_User())


async def _unused():  # pragma: no cover - reached only if the gate lets a call through
    raise AssertionError('the upload was read even though the caller may not write')


async def test_writing_is_allowed_with_both_keys(monkeypatch):
    _menu_gate(monkeypatch, granted=WRITE)

    async def _create_term(**_kwargs):
        return _term()

    monkeypatch.setattr(terms.term_service, 'create_term', _create_term)

    response = await terms.create_term(terms.TermCreatePayload(standard_term='A', definition='a'), login_user=_User())

    assert response.data['id'] == 1


# AC-31 (no `PermissionService` anywhere in this package) is asserted in
# `test_invariants.py`, not here: that check spans the whole package, and a
# text-level scan in two places would only duplicate it — worse, a naive one
# cannot tell a call from a docstring that mentions one.


# ── routing: the literals must not be shadowed by the id route ───────────────


def test_no_route_shadows_the_import_paths():
    """AC-12's import dialog posts to `/import/preview`. An unconstrained
    `/{term_id}` would match that literal and answer from the wrong handler —
    a failure that looks like "the preview returns nonsense" rather than like a
    routing bug, and one that only shows up on the import dialog.

    Stated as a property over EVERY route rather than over the id route alone:
    per-route assertions pass as long as the one route named in the test keeps
    its convertor, which is how a second unconstrained route would slip in.
    """
    from bisheng.term_library.api.router import router

    literals = {'/term-library/import', '/term-library/import/preview'}

    for route in router.routes:
        for literal in literals:
            if route.path == literal:
                continue
            assert route.path_regex.match(literal) is None, (
                f'{route.path} also matches {literal}, so the import dialog hits the wrong handler'
            )

    paths = [route.path for route in router.routes]
    for literal in literals:
        # Registered first as well as constrained: order is what saves a
        # request when someone later drops a convertor.
        assert paths.index(literal) < paths.index('/term-library/{term_id:int}')
