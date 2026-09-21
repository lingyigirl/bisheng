"""F060 T-15: what this feature must NOT do (AC-26, AC-29 ~ AC-32 + INV-17/18).

Everything here is a negative assertion — "the injection path never reads the
department", "nothing in this package reaches for OpenFGA", "the knowledge
module does not know terms exist". That shape is why the file exists separately
from the behaviour tests: a negative is easy to assume and impossible to notice
breaking, so each one is checked two ways where a way exists:

  * **statically**, over the source — which catches a name appearing anywhere in
    a module, including a branch no test happens to drive; and
  * **behaviourally**, where there is a behaviour to drive — because a static
    scan cannot tell a live read from a dead one.

The static half needs care in the other direction too: a plain substring search
over source matches comments and docstrings. `test_term_api.py` already tripped
over that once (a docstring mentioning `PermissionService` failed a check for
CALLING it). So the scans below work on the parsed AST and only look at real
name loads — a module that documents the thing it must not do still passes.

These are also the assertions most likely to be quietly broken by a later
feature: someone adding department-scoped terms, or making the glossary a
knowledge type, would find these tests in the way — which is the point (contract
INV-17 / INV-18, spec AC-29 ~ AC-31).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import bisheng

BACKEND = pathlib.Path(bisheng.__file__).parent
TERM_PACKAGE = BACKEND / 'term_library'

#: Modules that can put a term block into a conversation prompt. INV-17 says the
#: department is display metadata and must not narrow what gets injected; these
#: are the files where reading it would do exactly that.
INJECTION_MODULES = (
    TERM_PACKAGE / 'domain/services/injection.py',
    TERM_PACKAGE / 'domain/services/prompt.py',
)


def _python_files(root: pathlib.Path):
    return sorted(path for path in root.rglob('*.py') if '__pycache__' not in path.parts)


def _loaded_names(path: pathlib.Path) -> set[str]:
    """Every name the module actually REFERENCES, from the parsed AST.

    Docstrings and comments are string literals, not `ast.Name` nodes, so a file
    that explains why it avoids something does not count as doing it.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit('.', 1)[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.rsplit('.', 1)[-1])
    return names


# ── INV-17: the department is display metadata, not a scope ──────────────────


@pytest.mark.parametrize('path', INJECTION_MODULES, ids=lambda p: p.name)
def test_the_injection_path_never_reads_the_department(path):
    """The neighbouring knowledge module uses `department_id` as a visibility
    boundary. Contract INV-17 says this one must not — the field only groups rows
    in the admin list, and reading it here would silently narrow which terms
    reach a conversation. It is the single most likely way a later change
    "improves" this feature into violating its own contract."""
    assert 'department_id' not in _loaded_names(path), (
        f'{path.name} reads department_id — the department must not affect injection (INV-17)'
    )


async def test_a_term_reaches_every_department_in_the_tenant(monkeypatch):
    """The behavioural half: two terms filed under different departments are both
    matched by a question that names both, regardless of who is asking."""
    from dataclasses import dataclass

    from bisheng.term_library.domain.services import injection

    @dataclass
    class _Term:
        id: int
        standard_term: str
        definition: str
        aliases: list
        department_id: int | None

    async def _alist_all():
        return [
            _Term(1, '不良贷款率', '比例', [], department_id=10),
            _Term(2, '拨备覆盖率', '拨备与不良的比', [], department_id=20),
        ]

    monkeypatch.setattr(injection.TermLibraryTermDao, 'alist_all', _alist_all)
    injection._reset_caches()

    block = await injection.build_term_block(7, '不良贷款率和拨备覆盖率分别是多少')

    assert '不良贷款率' in block
    assert '拨备覆盖率' in block, (
        'a term from another department was filtered out of the injection — the '
        'department is being treated as a scope (INV-17)'
    )


# ── INV-18: injection is read-only, for the length of one turn ───────────────


def test_the_injection_path_writes_no_persistent_configuration():
    """Contract INV-18. The block is built per turn and stored nowhere, which is
    also why an edit is visible on the next turn with no invalidation to reason
    about (AC-25). A `setattr` on an assistant or a config write here would make
    a per-turn value persistent and silently rewrite the user's prompt."""
    forbidden = {'assistant', 'assistant_message', 'ws_config', 'systemPrompt'}

    for path in INJECTION_MODULES:
        source = path.read_text(encoding='utf-8')
        for name in forbidden:
            assert f'{name}.' not in source and f'{name}[' not in source, (
                f'{path.name} writes to {name} — injection must not persist (INV-18)'
            )


# ── AC-31: no FGA, no PermissionService, no resource type ────────────────────


def test_the_term_package_never_calls_the_permission_service():
    """The library is tenant-level shared configuration, the same shape as the
    knowledge menu it sits behind — NOT a controlled resource. A
    `PermissionService` call here would mean per-term grants the contract does
    not define (design decision 2 / AC-31)."""
    for path in _python_files(TERM_PACKAGE):
        assert 'PermissionService' not in _loaded_names(path), (
            f'{path.relative_to(BACKEND)} reaches for PermissionService (AC-31)'
        )


def test_the_term_package_declares_no_fga_resource_type():
    """No tuple is written and no resource type is registered: the feature adds
    nothing to the authorization model."""
    forbidden = {'FgaResourceType', 'ResourceType', 'OpenFgaManager', 'ensure_relation', 'write_tuple'}

    for path in _python_files(TERM_PACKAGE):
        used = _loaded_names(path) & forbidden
        assert not used, f'{path.relative_to(BACKEND)} reaches for {sorted(used)} (AC-31)'


# ── AC-29 / AC-30: the knowledge module does not know terms exist ────────────


def test_terms_are_not_a_knowledge_type():
    """AC-29: no enum member, no collection, no index. A term is prompt text,
    not retrievable knowledge — giving it a type would put it in the RAG path,
    where it would be embedded, searched and authorized like a document."""
    from bisheng.knowledge.domain.models.knowledge import KnowledgeTypeEnum

    members = {member.name.lower() for member in KnowledgeTypeEnum}

    assert not any('term' in name for name in members), f'terms leaked into KnowledgeTypeEnum: {members}'


def test_the_knowledge_module_does_not_reference_terms():
    """AC-30: the knowledge package must not import, mention or depend on the
    term library in any direction. Checked over the AST rather than the text so
    a docstring about terms is not a failure."""
    offenders = []

    for path in _python_files(BACKEND / 'knowledge'):
        names = _loaded_names(path)
        if any('term_library' in name or name == 'term_library' for name in names):
            offenders.append(path.relative_to(BACKEND))
        source = path.read_text(encoding='utf-8')
        if 'from bisheng.term_library' in source or 'import bisheng.term_library' in source:
            offenders.append(path.relative_to(BACKEND))

    assert not offenders, f'the knowledge module depends on the term library: {sorted(set(offenders))} (AC-30)'


# ── AC-32: no terms means no change ──────────────────────────────────────────


async def test_an_empty_library_produces_no_block(monkeypatch):
    """The base case the other ACs rest on: without terms there is nothing to
    append, so every prompt is byte-identical to the pre-feature build."""
    from bisheng.term_library.domain.services import injection

    async def _alist_all():
        return []

    monkeypatch.setattr(injection.TermLibraryTermDao, 'alist_all', _alist_all)
    injection._reset_caches()

    assert await injection.build_term_block(7, '不良率是多少') == ''


async def test_a_blank_question_produces_no_block(monkeypatch):
    """No question, nothing to match — and no read is even attempted."""
    from bisheng.term_library.domain.services import injection

    async def _explode():
        raise AssertionError('the library was read for a blank question')

    monkeypatch.setattr(injection.TermLibraryTermDao, 'alist_all', _explode)
    injection._reset_caches()

    assert await injection.build_term_block(7, '   ') == ''


# ── AC-28 / AC-27: the enhancement never costs a conversation ────────────────


async def test_a_failing_library_read_yields_an_empty_block_not_an_error(monkeypatch):
    """Design §7.4 / AC-28: a glossary that cannot be read must not fail the
    turn. The user loses an enhancement, not their answer."""
    from bisheng.term_library.domain.services import injection

    async def _raise():
        raise RuntimeError('database is down')

    monkeypatch.setattr(injection.TermLibraryTermDao, 'alist_all', _raise)
    injection._reset_caches()

    assert await injection.build_term_block(7, '不良率是多少') == ''


def test_the_injection_module_declares_no_mutating_dao_calls():
    """AC-27's teeth: this feature must not change how a conversation behaves,
    and the only way it could is by writing something during a turn. Every DAO
    call the injection path makes is a read.

    The writer names come from the DAO's own source rather than a hardcoded
    list, so a writer added later is covered without anyone remembering to
    update this test — and the assertion that the scan found SOMETHING is what
    keeps a renamed DAO from turning this into a vacuous pass.
    """
    tree = ast.parse((TERM_PACKAGE / 'domain/models/dao.py').read_text(encoding='utf-8'))
    writers = {
        node.name
        for node in ast.walk(tree)
        # AsyncFunctionDef, not FunctionDef: these are `async def`, and matching
        # only the sync node type silently finds nothing.
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith(('acreate', 'aupdate', 'adelete'))
    }

    assert writers, 'no writer found in the DAO — its layout changed and this scan is now vacuous'

    injection_source = (TERM_PACKAGE / 'domain/services/injection.py').read_text(encoding='utf-8')
    for name in sorted(writers):
        assert name not in injection_source, f'injection.py calls the mutating DAO method {name} (AC-27)'
