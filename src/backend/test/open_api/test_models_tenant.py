"""F076 T-03:三张表模型的租户自动回填与关键行为。

覆盖:
- AC-01 / AC-07:服务账号按租户隔离;`tenant_id` 由 `before_flush` 自动回填
  (模型必须 `default=None`,见 test/tenant/test_tenant_id_default_guard.py)。
- design §4.2 的字段约定:密钥只存摘要、掩码形如 `bs-sak-********AbCd`、
  有效性为「未吊销且未过期」的单一判据(spec AC-12)。
"""

from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.open_api.domain.models.api_credential import (
    SERVICE_ACCOUNT_KEY_PREFIX,
    ApiCredential,
)
from bisheng.open_api.domain.models.open_api_tenant_setting import OpenApiTenantSetting
from bisheng.open_api.domain.models.service_account import ServiceAccount

_F076_TABLES = [
    ServiceAccount.__table__,
    ApiCredential.__table__,
    OpenApiTenantSetting.__table__,
]

TENANT_ID = 7


@pytest.fixture()
def f076_db(db_engine):
    """在共享 SQLite 引擎上补建 F076 三张表,并确保租户事件已注册。

    与 test/workstation/test_favorite_repository.py 同款做法:测试自建表 +
    显式注册过滤事件(`register_tenant_filter_events` 幂等)。
    """
    ServiceAccount.metadata.create_all(db_engine, tables=_F076_TABLES)
    register_tenant_filter_events()
    return db_engine


def test_service_account_tenant_id_autofill(f076_db, db_session, tenant_context):
    """未显式给 tenant_id 的服务账号行,落库时由当前租户上下文自动回填。"""
    tenant_context(TENANT_ID)

    account = ServiceAccount(name="信贷系统", resource_owner_user_id=42)
    db_session.add(account)
    db_session.commit()

    assert account.tenant_id == TENANT_ID
    # 单行可读回(同一租户上下文内)
    fetched = db_session.exec(select(ServiceAccount).where(ServiceAccount.name == "信贷系统")).one()
    assert fetched.is_enabled is True


def test_service_account_disabled_state(f076_db, db_session, tenant_context):
    """停用/软删即 `is_enabled=False`(spec AC-05 / AC-06 的状态语义)。"""
    tenant_context(TENANT_ID)
    account = ServiceAccount(name="渠道系统", resource_owner_user_id=43, disabled_at=datetime.now())
    db_session.add(account)
    db_session.commit()
    assert account.is_enabled is False


def test_api_credential_tenant_autofill_and_mask(f076_db, db_session, tenant_context):
    """密钥行租户回填 + 掩码只回末四位(spec AC-08;明文不落库)。"""
    tenant_context(TENANT_ID)

    credential = ApiCredential(
        service_account_id=1,
        name="生产密钥",
        last4="AbCd",
        token_hash="a" * 64,
        scopes=["workflow:invoke", "knowledge:read"],
    )
    db_session.add(credential)
    db_session.commit()

    assert credential.tenant_id == TENANT_ID
    assert credential.key_mask == f"{SERVICE_ACCOUNT_KEY_PREFIX}********AbCd"


def test_api_credential_validity_single_predicate(f076_db, db_session, tenant_context):
    """有效性 = 未吊销且未过期;过期判定排他(spec AC-11 / AC-12)。"""
    tenant_context(TENANT_ID)
    now = datetime.now()

    credential = ApiCredential(service_account_id=1, last4="WxYz", token_hash="b" * 64, scopes=[])
    db_session.add(credential)
    db_session.commit()

    # 无有效期 → 有效
    assert credential.is_valid_at(now) is True

    # 恰好到期时刻 → 无效(排他)
    credential.expires_at = now
    assert credential.is_valid_at(now) is False

    # 已吊销 → 无效(即便未过期)
    credential.expires_at = None
    credential.revoked_at = now
    assert credential.is_valid_at(now) is False


def test_tenant_setting_missing_row_means_not_enforced(f076_db, db_session, tenant_context):
    """租户策略表:缺行 = 未启用(fail-closed,spec AC-34 的一半语义)。"""
    tenant_context(TENANT_ID)
    assert db_session.exec(select(OpenApiTenantSetting)).all() == []

    setting = OpenApiTenantSetting(tenant_id=TENANT_ID, enforce_keys=True)
    db_session.add(setting)
    db_session.commit()
    assert setting.enforce_keys is True


def test_expired_credential_boundary(f076_db, db_session, tenant_context):
    """边界:未来 1 秒到期仍有效、过去 1 秒到期即无效。"""
    tenant_context(TENANT_ID)
    credential = ApiCredential(service_account_id=1, last4="EfGh", token_hash="c" * 64, scopes=[])
    now = datetime.now()

    credential.expires_at = now + timedelta(seconds=1)
    assert credential.is_valid_at(now) is True

    credential.expires_at = now - timedelta(seconds=1)
    assert credential.is_valid_at(now) is False
