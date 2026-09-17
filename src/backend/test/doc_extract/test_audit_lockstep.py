"""Guards the audit-visibility whitelist against silent drift.

`ainsert_v2` does not set `system_id`, so whether an audit row is reachable from
the audit page depends entirely on its action being in `_UI_VISIBLE_V2_ACTIONS`.
A missing entry writes a row nobody can see, with no error anywhere -- the exact
failure mode the enum in `domain/constants.py` exists to prevent.

The frontend mirror (`platform/src/controllers/API/log.ts`) is asserted too,
because the backend and frontend lists are maintained by hand in two repos-side
files and the file's own comment says they must stay in sync.
"""

from pathlib import Path

from bisheng.database.models.audit_log import (
    _UI_VISIBLE_V2_ACTIONS,
    _V2_NAMESPACE_TO_ACTION_PREFIX,
)
from bisheng.doc_extract.domain.constants import DOC_EXTRACT_AUDIT_ACTIONS

REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_TS = REPO_ROOT / 'frontend' / 'platform' / 'src' / 'controllers' / 'API' / 'log.ts'


def test_all_doc_extract_actions_are_ui_visible():
    missing = [a for a in DOC_EXTRACT_AUDIT_ACTIONS if a not in _UI_VISIBLE_V2_ACTIONS]
    assert not missing, f'audit rows would be written but unreachable: {missing}'


def test_doc_extract_namespace_is_mapped():
    """The audit page's module filter maps a synthetic namespace to an action
    prefix; without this the new rows cannot be filtered by module."""
    assert _V2_NAMESPACE_TO_ACTION_PREFIX.get('doc_extract') == 'doc_extract.'


def test_doc_extract_actions_share_the_namespace_prefix():
    """Otherwise the module filter would not actually select them."""
    for action in DOC_EXTRACT_AUDIT_ACTIONS:
        assert action.startswith('doc_extract.'), action


def test_frontend_mirror_lists_every_action():
    if not LOG_TS.exists():  # pragma: no cover - frontend not checked out
        return
    content = LOG_TS.read_text(encoding='utf-8')
    missing = [a for a in DOC_EXTRACT_AUDIT_ACTIONS if f"value: '{a}'" not in content]
    assert not missing, (
        f'frontend log.ts is missing these actions, so the audit page filter '
        f'will not offer them: {missing}'
    )
