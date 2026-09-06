"""T003 — F051 workflow version compare endpoint static contract checks.

The pytest pre-mock chain (``test/conftest.py``) breaks live service
instantiation, so endpoint contracts are validated via source-code checks
(the established pattern in ``test/api/``). Behavior-level coverage lives in
``test/e2e/test_e2e_f051_flow_version_compare.py``.

Covers (spec AC-12/13/14 + design.md):
- endpoint declared with query params flow_id/from_version_id/to_version_id
- route order: ``/versions/compare`` declared BEFORE ``/versions/{version_id}``
  (design.md 坑 1 — otherwise int parsing 422s)
- ``FlowService.compare_versions`` uses the unified permission entry
  (ApplicationPermissionService, 'workflow', view_app/use_app — design 决策 3)
  and NotFoundVersionError (10500) for missing/mismatched versions
- diff is delegated to the pure function, no ORM queries in Service
  (constitution C1)
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parents[2] / 'bisheng' / rel).read_text(encoding='utf-8')


def _endpoint_func(src: str, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(src)
    return next(
        n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )


def _router_decl_order(src: str) -> list[str]:
    """Return the list of endpoint paths in declaration order (path literal only).

    Walks the module body in SOURCE order (``ast.walk`` is breadth-first and
    does not preserve declaration order across functions).
    """
    paths: list[str] = []
    for node in ast.parse(src).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            deco_src = ast.get_source_segment(src, deco) or ''
            match = re.search(r"router\.(?:get|post|put|delete)\s*\(\s*['\"]([^'\"]+)", deco_src)
            if match:
                paths.append(match.group(1))
    return paths


def test_compare_endpoint_declared_with_three_query_params():
    src = _read('api/v1/workflow.py')
    func = _endpoint_func(src, 'compare_versions')
    for param in ('flow_id', 'from_version_id', 'to_version_id'):
        assert param in {a.arg for a in func.args.kwonlyargs} | {a.arg for a in func.args.args}, (
            f'compare_versions must declare {param} query param'
        )


def test_compare_route_declared_before_version_detail_route():
    src = _read('api/v1/workflow.py')
    paths = _router_decl_order(src)
    # The GET detail route is '/versions/{version_id}' — compare only conflicts
    # with the GET family (PUT/DELETE can't parse a GET request), but the guard
    # demands it before the FIRST parameterized version route anyway.
    detail_idx = paths.index('/versions/{version_id}')
    compare_idx = paths.index('/versions/compare')
    assert compare_idx < detail_idx, (
        'GET /versions/compare must be declared before GET /versions/{version_id} '
        '(design.md 坑 1 — otherwise "compare" is parsed as int → 422)'
    )


def test_compare_endpoint_delegates_to_flow_service():
    src = _read('api/v1/workflow.py')
    func_src = ast.get_source_segment(src, _endpoint_func(src, 'compare_versions'))
    assert 'FlowService.compare_versions' in func_src


def test_flow_service_compare_versions_uses_unified_permission_entry():
    src = _read('api/services/flow.py')
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'FlowService')
    func = next(
        n for n in cls.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == 'compare_versions'
    )
    func_src = ast.get_source_segment(src, func)
    assert 'ApplicationPermissionService.has_any_permission_async' in func_src, (
        'permission must go through the unified entry point (constitution C4)'
    )
    assert "'workflow'" in func_src
    assert "'view_app'" in func_src and "'use_app'" in func_src
    assert 'UnAuthorizedError' in func_src


def test_flow_service_compare_versions_uses_10500_for_missing_version():
    src = _read('api/services/flow.py')
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'FlowService')
    func = next(
        n for n in cls.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == 'compare_versions'
    )
    func_src = ast.get_source_segment(src, func)
    assert 'NotFoundVersionError' in func_src
    assert 'compare_flow_versions' in func_src


def test_diff_module_has_no_domain_or_api_imports():
    src = _read('common/utils/flow_version_diff.py')
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(('import ', 'from ')):
            assert 'domain' not in stripped and 'api' not in stripped and 'database' not in stripped, (
                f'flow_version_diff.py must stay a pure function library (C1): {stripped}'
            )
