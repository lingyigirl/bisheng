"""Relation models pin themselves to a template SNAPSHOT the first time they are saved.

`FineGrainedPermissionService._permission_ids_for_relation` resolves a granted
relation to permission ids through four ordered rules: an explicit snapshot
wins, a non-empty `permissions` list wins, a built-in model with an empty list
falls through to the live template, and a custom model with an empty list grants
nothing.

Built-in models ship with `permissions: []` + `permissions_explicit: False`
(`relation_model_store.default_relation_models`), so they start on rule 3 and
track the code template. But the admin PUT route writes
`m["permissions_explicit"] = True` whenever the request carries a `permissions`
field (`permission/api/endpoints/resource_permission.py`), and the platform
sends that field on every save (`RolesAndPermissions.tsx` — create, save, and
reset all include it). So one save moves a built-in model onto rule 1, and
accepting the template's own values just pins the snapshot at today's ids:
later template changes silently stop reaching that model.

The backfill on startup (`relation_model_backfill.backfill_relation_model_move_permissions`)
exists to patch frozen models after a template change — evidence that the freeze
is expected and handled out of band, not a one-off.
"""

from bisheng.permission.domain.services.fine_grained_permission_service import (
    FineGrainedPermissionService,
)
from bisheng.permission.domain.services.relation_model_store import normalize_model_dict

_BUILTIN_VIEWER = {
    "id": "viewer",
    "name": "可查看",
    "relation": "viewer",
    "grant_tier": "usage",
    "permissions": [],
    "permissions_explicit": False,
    "is_system": True,
}


def _save(model: dict, permissions: list[str]) -> dict:
    """Apply what the admin PUT route does, then the read-side normalization.

    Route: `m["permissions"] = request.permissions; m["permissions_explicit"] = True`.
    Normalization runs on every read via `_get_relation_models_map`.
    """
    saved = dict(model)
    saved["permissions"] = list(permissions)
    saved["permissions_explicit"] = True
    return normalize_model_dict(saved)


def _effective(model: dict | None, relation: str = "viewer") -> set[str]:
    return FineGrainedPermissionService._permission_ids_for_relation(
        "knowledge_library",
        relation,
        model,
    )


def test_rule3_pristine_builtin_tracks_the_live_template(monkeypatch):
    """A built-in model that has never been saved follows the code template."""
    assert _effective(_BUILTIN_VIEWER) == {"view_kb", "use_kb"}

    # Change the template and the pristine model picks it up immediately: rule 3
    # re-reads the template on every evaluation.
    _patch_template(monkeypatch, {"view_kb", "use_kb", "new_kb_action"})
    assert _effective(_BUILTIN_VIEWER) == {"view_kb", "use_kb", "new_kb_action"}


def test_rule1_saving_freezes_the_model_so_a_later_template_edit_misses_it(monkeypatch):
    """The freeze: once saved, the stored snapshot is authoritative."""
    frozen = _save(_BUILTIN_VIEWER, ["view_kb", "use_kb"])
    assert frozen["permissions_explicit"] is True
    assert _effective(frozen) == {"view_kb", "use_kb"}

    # The tell: a template change reaches the pristine model but NOT the saved
    # one. This is the whole defect — the admin who saved is unaware their model
    # has stopped tracking the template.
    _patch_template(monkeypatch, {"view_kb", "use_kb", "new_kb_action"})
    assert _effective(_BUILTIN_VIEWER) == {"view_kb", "use_kb", "new_kb_action"}
    assert _effective(frozen) == {"view_kb", "use_kb"}


def test_rule1_empty_snapshot_grants_nothing_instead_of_falling_back(monkeypatch):
    """The case that distinguishes rule 1 from rule 2.

    A saved model with a non-empty `permissions` list is indistinguishable
    between rules 1 and 2 — both return the same set. An EMPTY saved list is
    where they diverge: rule 1 returns `set()` (the admin's selection was
    genuinely "nothing"), while a model without the explicit flag would fall
    through to rule 3 and get the template defaults.

    Reachable from the UI: uncheck every box on a built-in model and save.
    """
    emptied = _save(_BUILTIN_VIEWER, [])
    assert emptied["permissions_explicit"] is True
    assert emptied["permissions"] == []

    # Saved-with-nothing ⇒ nothing. It does NOT fall back to the template.
    assert _effective(emptied) == set()

    # Contrast: the same model untouched still follows the template, so the two
    # are genuinely different paths rather than the same answer twice.
    assert _effective(_BUILTIN_VIEWER) == {"view_kb", "use_kb"}

    # And a template change still cannot reach the frozen model.
    _patch_template(monkeypatch, {"view_kb", "use_kb", "new_kb_action"})
    assert _effective(emptied) == set()
    assert _effective(_BUILTIN_VIEWER) == {"view_kb", "use_kb", "new_kb_action"}


def test_rule4_custom_model_with_empty_permissions_grants_nothing():
    """A custom model with an empty list is NOT a template follower."""
    custom = normalize_model_dict(
        {
            "id": "custom_deadbeef",
            "name": "自定义",
            "relation": "viewer",
            "permissions": [],
            "is_system": False,
        }
    )
    assert custom["permissions_explicit"] is False
    assert _effective(custom) == set()


def test_rule5_no_model_falls_back_to_the_template(monkeypatch):
    """An unmatched binding (model=None) falls back to the live template."""
    assert _effective(None) == {"view_kb", "use_kb"}
    _patch_template(monkeypatch, {"view_kb", "use_kb", "new_kb_action"})
    assert _effective(None) == {"view_kb", "use_kb", "new_kb_action"}


def test_normalization_does_not_invent_explicit_for_pristine_builtins():
    """Read-side normalization must not freeze a built-in on its own.

    Only the write path sets `permissions_explicit`. If normalization ever set
    it for built-ins, every model would freeze on first read instead of first
    save.
    """
    pristine = normalize_model_dict(
        {
            "id": "editor",
            "name": "可编辑",
            "relation": "editor",
            "permissions": [],
            "is_system": True,
        }
    )
    assert pristine["permissions_explicit"] is False

    # A custom model with ids is explicit by inference — normalize() fills the
    # flag in when the field is absent.
    custom_with_ids = normalize_model_dict(
        {
            "id": "custom_abc12345",
            "name": "自定义",
            "relation": "editor",
            "permissions": ["view_kb"],
            "is_system": False,
        }
    )
    assert custom_with_ids["permissions_explicit"] is True


def _patch_template(monkeypatch, permission_ids: set[str]) -> None:
    """Make the knowledge-library template resolve to `permission_ids`.

    Patches the dispatcher the rule-3/rule-5 branches call, which is the single
    place the template enters `_permission_ids_for_relation`.
    """
    monkeypatch.setattr(
        FineGrainedPermissionService,
        "default_permission_ids_for_relation",
        staticmethod(lambda object_type, relation: set(permission_ids)),
    )
