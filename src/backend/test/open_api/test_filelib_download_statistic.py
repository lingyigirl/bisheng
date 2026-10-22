"""`GET /api/v2/filelib/download_statistic` 的权限位拆分与路径加固(2026-10-22)。

旧实现有两个问题:
1. 挂在 `knowledge:read` 下,但读服务器日志不需要任何知识资源,scope 语义错位;
2. 用 `dir_path.startswith("/app/data")` 做字符串前缀判断,`/app/database/...`
   同样满足前缀,可越出诊断目录。

本测试锁定:端点只由 `diagnostics:read` 覆盖;路径经 realpath 解析后必须落在
诊断根目录内,且必须是已存在的 `.log` 文件。
"""

import os

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from bisheng.open_api.domain.scopes import OPEN_API_SCOPES
from bisheng.open_endpoints.api.endpoints import filelib

_ENDPOINT = ("GET", "/api/v2/filelib/download_statistic")


def _endpoints_of(scope_code: str) -> set[tuple[str, str]]:
    scope = next(item for item in OPEN_API_SCOPES if item.code == scope_code)
    return set(scope.endpoints)


def test_download_statistic_belongs_to_diagnostics_scope_only():
    assert _ENDPOINT in _endpoints_of("diagnostics:read")
    # 回归:不能再借 knowledge:read 读日志。
    assert _ENDPOINT not in _endpoints_of("knowledge:read")


@pytest.fixture
def diagnostics_root(tmp_path, monkeypatch):
    """把诊断根目录指向临时目录,避免测试依赖 /app/data。"""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(filelib, "_DIAGNOSTICS_ROOT", str(root))
    return root


def test_allows_log_inside_root(diagnostics_root):
    log_file = diagnostics_root / "worker.log"
    log_file.write_text("line\n", encoding="utf-8")

    response = filelib.download_statistic_file(str(log_file))

    assert isinstance(response, FileResponse)
    assert response.filename == "worker.log"
    assert os.path.realpath(response.path) == os.path.realpath(log_file)


def test_allows_nested_log_inside_root(diagnostics_root):
    nested = diagnostics_root / "sub" / "dir"
    nested.mkdir(parents=True)
    log_file = nested / "task.log"
    log_file.write_text("line\n", encoding="utf-8")

    response = filelib.download_statistic_file(str(log_file))

    assert isinstance(response, FileResponse)


def test_rejects_sibling_prefix_lookalike(diagnostics_root, tmp_path):
    """`/app/database/x.log` 这类同前缀兄弟目录必须拒绝(旧实现的绕过点)。"""
    sibling = tmp_path / "database"
    sibling.mkdir()
    log_file = sibling / "secret.log"
    log_file.write_text("secret\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        filelib.download_statistic_file(str(log_file))

    assert exc.value.status_code == 500


def test_rejects_traversal_out_of_root(diagnostics_root, tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_text("outside\n", encoding="utf-8")
    traversal = str(diagnostics_root / ".." / "outside.log")

    with pytest.raises(HTTPException):
        filelib.download_statistic_file(traversal)


def test_rejects_symlink_escaping_root(diagnostics_root, tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_text("outside\n", encoding="utf-8")
    link = diagnostics_root / "link.log"
    link.symlink_to(outside)

    with pytest.raises(HTTPException):
        filelib.download_statistic_file(str(link))


def test_allows_symlink_resolving_inside_root(diagnostics_root):
    """根目录内的轮转软链允许:realpath 解析后仍在根内。"""
    target = diagnostics_root / "app.log.1"
    target.write_text("line\n", encoding="utf-8")
    link = diagnostics_root / "app.log"
    link.symlink_to(target)

    response = filelib.download_statistic_file(str(link))

    assert isinstance(response, FileResponse)


def test_rejects_non_log_suffix(diagnostics_root):
    other = diagnostics_root / "dump.txt"
    other.write_text("text\n", encoding="utf-8")

    with pytest.raises(HTTPException):
        filelib.download_statistic_file(str(other))


def test_rejects_missing_file(diagnostics_root):
    with pytest.raises(HTTPException):
        filelib.download_statistic_file(str(diagnostics_root / "ghost.log"))


def test_rejects_directory_with_log_suffix(diagnostics_root):
    """`isfile` 校验:名字像日志的目录不能下载。"""
    directory = diagnostics_root / "dir.log"
    directory.mkdir()

    with pytest.raises(HTTPException):
        filelib.download_statistic_file(str(directory))


def test_rejects_relative_path(diagnostics_root):
    with pytest.raises(HTTPException):
        filelib.download_statistic_file("app/data/worker.log")
