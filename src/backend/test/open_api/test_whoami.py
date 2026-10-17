"""F076 T-15:whoami 自检端点(spec AC-33)。

直接调用端点函数(不经 HTTP 栈):principal 由依赖安装,这里验证
"有 principal 回显自描述信息 / 无 principal 一律 11801"两条语义。
"""

from datetime import datetime

import pytest

from bisheng.common.errcode.open_api import OpenApiKeyMissingError
from bisheng.open_api.api.endpoints.auth import whoami
from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    reset_open_api_principal,
    set_open_api_principal,
)


class _StubAccount:
    def __init__(self, name: str):
        self.name = name


@pytest.fixture()
def principal_installed(monkeypatch):
    async def _account(account_id: int):
        return _StubAccount("信贷系统")

    monkeypatch.setattr("bisheng.open_api.api.endpoints.auth.ServiceAccountDao.aget_by_id", _account)

    principal = OpenApiPrincipal(
        credential_id=77,
        tenant_id=9,
        service_account_id=5,
        owner_user_id=900,
        key_mask="bs-sak-********AbCd",
        scopes=("workflow:invoke", "knowledge:read"),
        owner_payload=object(),
        expires_at=datetime(2030, 1, 1, 0, 0, 0),
    )
    token = set_open_api_principal(principal)
    yield principal
    reset_open_api_principal(token)


async def test_whoami_without_principal_is_rejected():
    """总闸关闭(依赖不装 principal)或裸调用:一律按缺密钥拒绝。"""
    with pytest.raises(OpenApiKeyMissingError):
        await whoami()


async def test_whoami_echoes_self_description(principal_installed):
    response = await whoami()
    data = response.data

    assert data["account_id"] == 5
    assert data["account_name"] == "信贷系统"
    assert data["key_mask"] == "bs-sak-********AbCd"
    assert data["scopes"] == ["workflow:invoke", "knowledge:read"]
    assert data["expires_at"] == "2030-01-01T00:00:00"
    assert data["tenant_id"] == 9


async def test_whoami_without_expiry_reports_null(principal_installed):
    """不过期的密钥:expires_at 回 null(而不是编造一个时间)。"""
    from dataclasses import replace

    token = set_open_api_principal(replace(principal_installed, expires_at=None))
    try:
        response = await whoami()
        assert response.data["expires_at"] is None
    finally:
        reset_open_api_principal(token)
