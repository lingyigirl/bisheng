"""接入指南地址来源：运维配置优先，本机探测值兜底。

对应 `GET /api/v1/open-api/base-url`。核心不变量：探测值**永远不覆盖**配置值——
反向代理/HTTPS 部署下进程看不到对外地址，猜测值可能被防火墙挡住。
"""

import pytest
from pydantic import ValidationError

from bisheng.core.config.open_api import OpenApiConf
from bisheng.open_api.api.endpoints import settings as settings_api
from bisheng.open_api.domain.services import base_url_service


class _Admin:
    tenant_id = 1
    user_id = 7


class _FakeRequest:
    def __init__(self, port: int | None):
        self.scope = {"server": ("0.0.0.0", port)}


# ---------------------------------------------------------------------------
# 配置项
# ---------------------------------------------------------------------------


def test_config_strips_trailing_slash():
    conf = OpenApiConf(public_base_url="https://gw.example.com/bisheng/")
    assert conf.public_base_url == "https://gw.example.com/bisheng"


def test_config_defaults_to_empty():
    assert OpenApiConf().public_base_url == ""


@pytest.mark.parametrize("value", ["ftp://gw.example.com", "gw.example.com", "http://"])
def test_config_rejects_invalid_url(value):
    with pytest.raises(ValidationError):
        OpenApiConf(public_base_url=value)


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


def test_detect_builds_http_url_from_ip_and_port():
    assert base_url_service.detect_local_base_url("10.0.0.5", 7860) == "http://10.0.0.5:7860"


@pytest.mark.parametrize(("ip", "port"), [(None, 7860), ("10.0.0.5", None), (None, None)])
def test_detect_gives_up_when_either_part_is_missing(ip, port):
    assert base_url_service.detect_local_base_url(ip, port) is None


def test_listen_port_reads_asgi_server_scope():
    assert base_url_service.request_listen_port(_FakeRequest(7860)) == 7860


@pytest.mark.parametrize("req", [None, _FakeRequest(None)])
def test_listen_port_missing_returns_none(req):
    # `request` 是 pytest 的保留参数名，这里用 req 避免与内置 fixture 冲突。
    assert base_url_service.request_listen_port(req) is None


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


async def test_endpoint_reports_configured_value(monkeypatch):
    monkeypatch.setattr(settings_api.app_settings, "open_api", OpenApiConf(public_base_url="https://gw.example.com"))
    monkeypatch.setattr(base_url_service, "primary_ipv4", lambda: "10.0.0.5")

    response = await settings_api.get_base_url(request=_FakeRequest(7860), login_user=_Admin())

    assert response.status_code == 200
    assert response.data == {"configured": "https://gw.example.com", "detected": "http://10.0.0.5:7860"}


async def test_endpoint_falls_back_to_detected_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings_api.app_settings, "open_api", OpenApiConf())
    monkeypatch.setattr(base_url_service, "primary_ipv4", lambda: "10.0.0.5")

    response = await settings_api.get_base_url(request=_FakeRequest(7860), login_user=_Admin())

    assert response.data == {"configured": "", "detected": "http://10.0.0.5:7860"}


async def test_endpoint_survives_undetectable_host(monkeypatch):
    """取不到 IP 时仍要 200——前端据此回退控制台源，不能因探测失败而报错。"""
    monkeypatch.setattr(settings_api.app_settings, "open_api", OpenApiConf())
    monkeypatch.setattr(base_url_service, "primary_ipv4", lambda: None)

    response = await settings_api.get_base_url(request=_FakeRequest(7860), login_user=_Admin())

    assert response.data == {"configured": "", "detected": None}
