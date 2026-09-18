"""Canonical datasource permission template (F061).

Backend source of truth for ``db_datasource`` permission ids, shaped like
``knowledge_library_permission_template.py`` and ``tool_permission_template.py``.

Why a separate template instead of reusing the knowledge-library one: spec
AC-44 requires datasource management to be independent of knowledge-library
management, so ``manage_datasource_*`` is a distinct id family. Reusing
``manage_kb_*`` would make one grant open both, and the two populations differ
in practice (a DBA runs the datasources, a content owner runs the libraries).

Spec AC-45/AC-46/AC-47 scope the shape here: creator-only by default, no
"everyone can see" option, and no department-ownership semantics — hence the
four grantable relations mirror the standard ReBAC pyramid (viewer < editor <
manager < owner) and nothing else.
"""

from __future__ import annotations

_RELATION_LEVEL: dict[str, int] = {
    "can_read": 1,
    "can_edit": 2,
    "can_manage": 3,
    "can_delete": 4,
}

_MODEL_LEVEL: dict[str, int] = {
    "viewer": 1,
    "editor": 2,
    "manager": 3,
    "owner": 4,
}
_COMPUTED_TO_MODEL_RELATION: dict[str, str] = {
    "can_read": "viewer",
    "can_edit": "editor",
    "can_manage": "manager",
    "can_delete": "owner",
}

DATASOURCE_PERMISSION_TEMPLATE: dict = {
    "title": "数据源模块",
    "columns": [
        {
            "title": "",
            "items": [
                {"id": "view_datasource", "label": "查看数据源", "relation": "can_read"},
                {"id": "use_datasource", "label": "使用数据源", "relation": "can_read"},
                {"id": "edit_datasource", "label": "编辑数据源", "relation": "can_edit"},
                {"id": "delete_datasource", "label": "删除数据源", "relation": "can_delete"},
            ],
        },
        {
            "title": "",
            "items": [
                {"id": "manage_datasource_owner", "label": "管理数据源所有者", "relation": "can_manage"},
                {"id": "manage_datasource_manager", "label": "管理数据源管理者", "relation": "can_manage"},
                {"id": "manage_datasource_viewer", "label": "管理数据源编辑者与使用者", "relation": "can_manage"},
            ],
        },
    ],
}


def datasource_template_sections() -> list[dict]:
    return DATASOURCE_PERMISSION_TEMPLATE["columns"]


def datasource_template_permissions() -> list[dict]:
    return [item for column in datasource_template_sections() for item in column["items"]]


def default_permission_ids_for_relation(relation: str) -> set[str]:
    normalized = _COMPUTED_TO_MODEL_RELATION.get(relation, relation)
    relation_level = _MODEL_LEVEL.get(normalized, 0)
    return {
        item["id"]
        for item in datasource_template_permissions()
        if relation_level >= _RELATION_LEVEL.get(item["relation"], 99)
    }
