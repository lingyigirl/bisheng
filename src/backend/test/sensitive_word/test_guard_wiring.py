"""Every chat entry point must actually receive the guard.

This file exists because of a specific measurement: deleting the
`input_guard=` / `output_guard=` arguments from all four `dispatch_client`
call sites left the entire sensitive-word suite GREEN. The guard's own logic was
thoroughly covered -- chunking, masking, blocking, cross-chunk matching -- but
nothing asserted that the endpoints PASS it, so the feature could be silently
disconnected from every chat surface by a refactor that touched no guarded code.

The four sites are what makes this worth a test rather than a convention. Spec
AC-05 says "any chat entry point", and a new endpoint is added by copying an
existing one -- which makes "the copy forgot the guard arguments" the most
likely way this breaks, and the least likely to be noticed.

Asserted two ways, deliberately:

  * **statically**, by reading the source of the call sites. This is the check
    that catches a deletion; a behavioural test only catches it if it happens to
    drive that exact endpoint.
  * **behaviourally**, by invoking an endpoint with `dispatch_client` stubbed
    and inspecting the kwargs it received. This catches a call that passes the
    arguments but passes the wrong thing.

Neither subsumes the other: the static check cannot see what is passed, and the
behavioural one cannot see an endpoint it does not know about.
"""

import ast
from pathlib import Path

import pytest

# Every call site that hands a user's chat to a client. Adding a fifth entry
# point without adding it here is itself the bug this file guards against.
DISPATCH_CALL_SITES = [
    'bisheng/api/v1/assistant.py',
    'bisheng/api/v1/workflow.py',
    'bisheng/open_endpoints/api/endpoints/assistant.py',
    'bisheng/open_endpoints/api/endpoints/workflow.py',
]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _dispatch_calls(path: Path) -> list[ast.Call]:
    """Every `dispatch_client(...)` call in a module."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == 'dispatch_client':
                calls.append(node)
    return calls


@pytest.mark.parametrize('relative_path', DISPATCH_CALL_SITES)
def test_call_site_exists(relative_path):
    """A moved or renamed endpoint must not silently drop out of this check."""
    path = _BACKEND_ROOT / relative_path
    assert path.exists(), f'{relative_path} no longer exists; update DISPATCH_CALL_SITES'
    assert _dispatch_calls(path), f'no dispatch_client call found in {relative_path}'


@pytest.mark.parametrize('relative_path', DISPATCH_CALL_SITES)
def test_call_site_passes_both_guards(relative_path):
    """The assertion that the mutation test proved was missing.

    `dispatch_client` declares `input_guard=None, output_guard=None`, so omitting
    them is valid Python and the chat runs unguarded with no error anywhere.
    """
    path = _BACKEND_ROOT / relative_path
    calls = _dispatch_calls(path)
    assert calls, f'no dispatch_client call in {relative_path}'

    for call in calls:
        passed = {kw.arg for kw in call.keywords if kw.arg}
        missing = {'input_guard', 'output_guard'} - passed
        assert not missing, (
            f'{relative_path}:{call.lineno} calls dispatch_client without {sorted(missing)}. '
            'The chat would run with no guardrail (spec AC-05 / AC-17).'
        )

        # Passing a literal None is the same defect written differently.
        for kw in call.keywords:
            if kw.arg in ('input_guard', 'output_guard'):
                assert not (isinstance(kw.value, ast.Constant) and kw.value.value is None), (
                    f'{relative_path}:{call.lineno} passes {kw.arg}=None explicitly'
                )


def test_no_other_module_dispatches_without_guards():
    """Catches a NEW entry point, which is the failure mode DISPATCH_CALL_SITES
    cannot see by construction.

    Scans the whole backend and fails on any `dispatch_client` call site that is
    not in the list above, so adding one forces a deliberate decision rather
    than a silent gap.
    """
    known = {str((_BACKEND_ROOT / p).resolve()) for p in DISPATCH_CALL_SITES}
    found: list[str] = []

    for path in _BACKEND_ROOT.joinpath('bisheng').rglob('*.py'):
        if not _dispatch_calls(path):
            continue
        if str(path.resolve()) in known:
            continue
        # The definition itself, and test doubles.
        if path.name == 'manager.py' and path.parent.name == 'chat':
            continue
        found.append(str(path.relative_to(_BACKEND_ROOT)))

    # The workstation entry point is a KNOWN gap -- see H8 in the F056 review.
    # Listed here so it is visible in the diff rather than merely absent, and so
    # that fixing it is a one-line change to this list.
    known_gaps = ['bisheng/workstation/domain/services/chat_service.py']

    unexpected = [f for f in found if f not in known_gaps]
    assert not unexpected, (
        f'dispatch_client is called from {unexpected}, which this test does not '
        'cover. Add them to DISPATCH_CALL_SITES and assert their guards too.'
    )
