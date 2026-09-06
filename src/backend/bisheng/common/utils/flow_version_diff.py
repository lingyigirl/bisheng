"""Pure diff for two ``flow_version.data`` snapshots (F051-flow-version-compare).

Normalization rules (design.md 决策 2):
- Nodes are matched by ``node.id`` and only ``node.data`` is compared —
  ``position`` and the top-level ``viewport`` are layout noise, never business
  differences (same口径 as the editor's unsaved-change check).
- Lists whose items are dicts carrying a ``key`` field (e.g. ``group_params`` /
  ``params`` entries) are aligned BY KEY so a param reorder produces no diff;
  any other list is aligned by index.
- Edges are matched by the connection tuple ``(source, target, sourceHandle,
  targetHandle)`` — ``edge.id`` is generated (``xy-edge__...``) and ignored.
- Missing vs empty (design.md 决策 2): a key on one side only is an
  added/removed field (``None`` on the absent side); a key on both sides with
  differing values (including ``None`` vs ``""``) is a modification.

This module is a pure function library: no DB access, no imports from
``domain/`` or ``api/`` (constitution C1).
"""

from typing import Any


def _alignable_by_key(items: list[Any]) -> bool:
    return bool(items) and all(isinstance(item, dict) and item.get('key') is not None for item in items)


def _diff(a: Any, b: Any, path: str) -> list[dict]:
    """Recursively diff two values, returning ``[{path, old_value, new_value}]``."""
    if isinstance(a, dict) and isinstance(b, dict):
        fields: list[dict] = []
        for key in sorted(set(a) | set(b)):
            child_path = f'{path}.{key}' if path else str(key)
            if key not in a:
                fields.append({'path': child_path, 'old_value': None, 'new_value': b[key]})
            elif key not in b:
                fields.append({'path': child_path, 'old_value': a[key], 'new_value': None})
            else:
                fields.extend(_diff(a[key], b[key], child_path))
        return fields

    if isinstance(a, list) and isinstance(b, list):
        if _alignable_by_key(a) and _alignable_by_key(b):
            fields = []
            keys_a = {item['key'] for item in a}
            keys_b = {item['key'] for item in b}
            # str() keeps mixed-type keys (e.g. str vs int param keys) sortable
            for key in sorted(keys_a | keys_b, key=lambda k: str(k)):
                child_path = f'{path}[{key}]'
                item_a = next((item for item in a if item['key'] == key), None)
                item_b = next((item for item in b if item['key'] == key), None)
                if item_a is None:
                    # Param-level add/remove: the field path lands on the param
                    # itself and carries its `value` (display-friendly "参数 X:
                    # 无 → v"), unlike both-sides-present params which recurse
                    # into `...params[X].value`.
                    fields.append({'path': child_path, 'old_value': None, 'new_value': item_b.get('value')})
                elif item_b is None:
                    fields.append({'path': child_path, 'old_value': item_a.get('value'), 'new_value': None})
                else:
                    fields.extend(_diff(item_a, item_b, child_path))
            return fields

        fields = []
        for index in range(max(len(a), len(b))):
            child_path = f'{path}[{index}]'
            if index >= len(a):
                fields.append({'path': child_path, 'old_value': None, 'new_value': b[index]})
            elif index >= len(b):
                fields.append({'path': child_path, 'old_value': a[index], 'new_value': None})
            else:
                fields.extend(_diff(a[index], b[index], child_path))
        return fields

    if a != b:
        return [{'path': path, 'old_value': a, 'new_value': b}]
    return []


def _node_key(node: dict) -> str:
    return node['id']


def _edge_key(edge: dict) -> tuple:
    return (edge.get('source'), edge.get('target'), edge.get('sourceHandle'), edge.get('targetHandle'))


def compare_flow_versions(data_from: dict, data_to: dict) -> dict:
    """Compare two version data snapshots.

    Returns::

        {
            "changed": bool,
            "node_changes": [
                {"node_id", "node_name", "node_type",
                 "change": "added" | "removed" | "modified",
                 "fields": [{"path", "old_value", "new_value"}]},  # modified only
            ],
            "edge_changes": [
                {"source", "target", "sourceHandle", "targetHandle",
                 "change": "added" | "removed"},
            ],
        }
    """
    nodes_from = {_node_key(n): n for n in data_from.get('nodes') or []}
    nodes_to = {_node_key(n): n for n in data_to.get('nodes') or []}

    node_changes: list[dict] = []
    for node_id in sorted(set(nodes_from) | set(nodes_to)):
        node_from = nodes_from.get(node_id)
        node_to = nodes_to.get(node_id)
        if node_from is None:
            data = node_to.get('data') or {}
            node_changes.append(
                {
                    'node_id': node_id,
                    'node_name': data.get('name'),
                    'node_type': data.get('type'),
                    'change': 'added',
                }
            )
            continue
        if node_to is None:
            data = node_from.get('data') or {}
            node_changes.append(
                {
                    'node_id': node_id,
                    'node_name': data.get('name'),
                    'node_type': data.get('type'),
                    'change': 'removed',
                }
            )
            continue

        fields = _diff(node_from.get('data'), node_to.get('data'), '')
        if fields:
            data = node_to['data']
            node_changes.append(
                {
                    'node_id': node_id,
                    'node_name': data.get('name'),
                    'node_type': data.get('type'),
                    'change': 'modified',
                    'fields': fields,
                }
            )

    edges_from = {_edge_key(e) for e in data_from.get('edges') or []}
    edges_to = {_edge_key(e) for e in data_to.get('edges') or []}
    edge_changes: list[dict] = []
    for edge in sorted(edges_from - edges_to):
        edge_changes.append(
            {'source': edge[0], 'target': edge[1], 'sourceHandle': edge[2], 'targetHandle': edge[3], 'change': 'removed'}
        )
    for edge in sorted(edges_to - edges_from):
        edge_changes.append(
            {'source': edge[0], 'target': edge[1], 'sourceHandle': edge[2], 'targetHandle': edge[3], 'change': 'added'}
        )

    return {
        'changed': bool(node_changes) or bool(edge_changes),
        'node_changes': node_changes,
        'edge_changes': edge_changes,
    }
