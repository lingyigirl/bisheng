"""F076 T-27 契约测试:开放 API 审计(逐调用 + 管理动作)。

钉住四件事(T-27 行的验证标准):
1. **写失败不改调用结果**——落库抛异常 / 队列满,都必须安静降级;
2. **一次调用一条**——批量落库后恰好一行,且字段齐(掩码/账号/scope/状态/码/耗时/IP);
3. **管理动作有行**——含操作者与前后取值,且**不含**明文/摘要;
4. **不进审计页白名单**——`open_api.*` 不得出现在 `_UI_VISIBLE_V2_ACTIONS`。

落库由 conftest 的 `audit_writes` 夹具换成内存收集(见该夹具 docstring):用例断言
"写了什么行",不碰真库;真正写库那一步是平台自己的 DAO,另有集成环境覆盖。
"""

import asyncio

from bisheng.database.models.audit_log import (
    _UI_VISIBLE_V2_ACTIONS,
    _V2_NAMESPACE_TO_ACTION_PREFIX,
    AuditLogDao,
)
from bisheng.open_api.domain.constants import OpenApiAuditAction
from bisheng.open_api.domain.services import call_audit_service

TENANT_ID = 77


class _FakeLoginUser:
    user_id = 42
    user_name = "admin"


def _record_call(**overrides):
    payload = {
        "tenant_id": TENANT_ID,
        "credential_id": 9,
        "service_account_id": 5,
        "owner_user_id": 100,
        "key_mask": "bs-sak-********AbCd",
        "scopes": ["workflow:invoke"],
        "http_status": 200,
        "biz_code": 200,
        "duration_ms": 12,
        "ip_address": "10.0.0.1",
        "endpoint": "/api/v2/workflow/invoke",
        "method": "POST",
        "required_scope": "workflow:invoke",
        "owner_name": "svc-对接系统",
    }
    payload.update(overrides)
    call_audit_service.record_call(**payload)


# ---------------------------------------------------------------------------
# 1) 一次调用一条 + 字段齐 + 无敏感值
# ---------------------------------------------------------------------------


async def test_call_audit_records_one_row(audit_writes):
    _record_call()
    await call_audit_service.flush_for_test()

    assert len(audit_writes) == 1
    row = audit_writes[0]
    assert row.action == OpenApiAuditAction.CALL.value
    assert row.target_type == "service_account"
    assert row.target_id == "5"
    # 操作者 = 专用身份(design 决策 1:与真人共享身份语义,靠 actor_kind + credential_id 区分)
    assert row.operator_id == 100
    assert row.tenant_id == TENANT_ID
    assert row.ip_address == "10.0.0.1"
    assert row.operator_name == "svc-对接系统"

    metadata = row.audit_metadata
    for key in (
        "key_mask",
        "credential_id",
        "scopes",
        "http_status",
        "biz_code",
        "duration_ms",
        "endpoint",
        "method",
        "required_scope",
    ):
        assert key in metadata, key
    assert metadata["actor_kind"] == "service_account"
    # 明文/摘要绝不出现(AC-09 / INV-23)
    assert "plaintext" not in str(metadata)
    assert "token_hash" not in str(metadata)


async def test_each_call_produces_exactly_one_row(audit_writes):
    for _ in range(5):
        _record_call()
    await call_audit_service.flush_for_test()
    assert len(audit_writes) == 5


async def test_failed_call_is_recorded_with_its_real_status(audit_writes):
    """失败调用同样留痕:HTTP 状态与业务码取客户端真正看到的那个。"""
    _record_call(http_status=403, biz_code=11807, reason="open_api_error")
    await call_audit_service.flush_for_test()
    metadata = audit_writes[0].audit_metadata
    assert metadata["http_status"] == 403 and metadata["biz_code"] == 11807
    assert audit_writes[0].reason == "open_api_error"


# ---------------------------------------------------------------------------
# 2) 写失败 / 队列满都不改变调用结果
# ---------------------------------------------------------------------------


async def test_flush_failure_is_swallowed(audit_writes, monkeypatch):
    async def _boom(cls, audit_logs):
        raise RuntimeError("db down")

    monkeypatch.setattr(AuditLogDao, "ainsert_audit_logs", classmethod(_boom))
    _record_call()
    await call_audit_service.flush_for_test()  # 不抛 = 通过(AC-36)
    assert audit_writes == []


async def test_queue_full_drops_without_blocking(audit_writes, monkeypatch):
    async def _noop_consumer():
        await asyncio.sleep(3600)

    monkeypatch.setattr(call_audit_service, "_consume_forever", _noop_consumer)
    monkeypatch.setattr(call_audit_service, "QUEUE_MAXSIZE", 2)

    for _ in range(5):  # 队列只装 2 条,其余应安静丢弃
        _record_call()
    assert call_audit_service.dropped_count() == 3

    call_audit_service.reset_for_test()
    assert call_audit_service.dropped_count() == 0


async def test_rejected_after_authentication_is_audited(audit_writes):
    """过了凭据校验才被拒的调用(如 11807 缺权限位)要留痕;凭据本身无效的不记。

    后者没有可归因身份,记了就是给无效密钥扫描留一个灌表的口子(design §5 坑 19)。
    """
    from starlette.requests import Request

    from bisheng.common.errcode.open_api import OpenApiScopeMissingError
    from bisheng.open_api.api.dependencies import _audit_rejected_call

    conn = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v2/assistant/list",
            "headers": [],
            "client": ("10.0.0.9", 1234),
            "query_string": b"",
        }
    )
    exc = OpenApiScopeMissingError(**{"required": "assistant:read"})

    # 没过凭据校验:不记
    _audit_rejected_call(conn, {}, 0.0, exc)
    await call_audit_service.flush_for_test()
    assert audit_writes == []

    class _Credential:
        id = 7
        tenant_id = 3
        service_account_id = 5
        key_mask = "bs-sak-********ZZZZ"
        scopes = ["chat:invoke"]

    class _Account:
        resource_owner_user_id = 100

    _audit_rejected_call(conn, {"credential": _Credential(), "account": _Account()}, 0.0, exc)
    await call_audit_service.flush_for_test()
    assert len(audit_writes) == 1
    row = audit_writes[0]
    assert row.audit_metadata["http_status"] == 403 and row.audit_metadata["biz_code"] == 11807
    assert row.audit_metadata["key_mask"] == "bs-sak-********ZZZZ"
    assert row.ip_address == "10.0.0.9"
    assert row.reason == "open_api_error"


# ---------------------------------------------------------------------------
# 3) 管理动作有行(含前后值,不含敏感值)
# ---------------------------------------------------------------------------


async def test_management_action_records_before_after(audit_writes):
    call_audit_service.record_admin_action(
        login_user=_FakeLoginUser(),
        tenant_id=TENANT_ID,
        operator_tenant_id=TENANT_ID,
        action=OpenApiAuditAction.API_KEY_ISSUE.value,
        target_type="api_credential",
        target_id=123,
        before={"key_masks": ["bs-sak-********WXYZ"]},
        after={"key_mask": "bs-sak-********AbCd", "scopes": ["assistant:invoke"]},
        extra={"service_account_id": 5},
        ip_address="10.0.0.2",
    )
    await call_audit_service.flush_for_test()

    assert len(audit_writes) == 1
    row = audit_writes[0]
    assert row.action == OpenApiAuditAction.API_KEY_ISSUE.value
    assert row.operator_id == 42
    assert row.operator_name == "admin"
    assert row.target_id == "123"
    assert row.ip_address == "10.0.0.2"
    assert row.audit_metadata["before"]["key_masks"] == ["bs-sak-********WXYZ"]
    assert row.audit_metadata["after"]["key_mask"] == "bs-sak-********AbCd"
    assert "plaintext" not in str(row.audit_metadata)


# ---------------------------------------------------------------------------
# 4) 不进审计页白名单(T-27 验证标准的 grep 项,做成可执行断言)
# ---------------------------------------------------------------------------


def test_open_api_actions_are_not_ui_visible():
    values = {action.value for action in OpenApiAuditAction}
    assert values, "动作词表不能为空"
    for value in values:
        assert value not in _UI_VISIBLE_V2_ACTIONS, f"{value} 不得出现在审计页白名单里"
    # 模块前缀映射也不得有 open_api——有的话审计页按模块筛就能筛出这些行。
    assert "open_api" not in _V2_NAMESPACE_TO_ACTION_PREFIX
    assert all(not value.startswith(("tenant.", "llm.server.", "doc_extract.", "approval.")) for value in values)


def test_action_names_follow_the_fixed_prefixes():
    """动作名只能落在 design §6.1 固定的四棵树内(裸串拼错会行入库但永远查不到)。"""
    prefixes = ("open_api.call", "open_api.service_account.", "open_api.api_key.", "open_api.settings.")
    for action in OpenApiAuditAction:
        assert action.value.startswith(prefixes), action.value
