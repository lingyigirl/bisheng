"""F076 T-17:服务账号 domain 服务的集成测试(spec AC-01~AC-06a)。

覆盖 tasks.md 的三条验收:
- 创建失败无残留(用"同租户重名"触发真实的唯一约束失败 → 断言身份/成员行一起回滚);
- owner 为管理员 → 11810(创建/变更的第一重防提权);
- owner 事后被提权 → 11816(与 T-06 鉴权依赖串联的运行期第二重)。

外加启停/删除的生命周期语义:停用即吊销全部密钥、删除即停用专用身份并软删账号。
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from starlette.requests import Request

from bisheng.common.errcode.open_api import (
    OpenApiKeyRevokedError,
    OpenApiOwnerNotAllowedError,
    OpenApiOwnerPrivilegedError,
)
from bisheng.database.models.tenant import UserTenant
from bisheng.open_api.api import dependencies as deps
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.scopes import open_api_scope
from bisheng.open_api.domain.services import credential_service as cred_service
from bisheng.open_api.domain.services import credential_validator as validator
from bisheng.open_api.domain.services import identity_service, tenant_setting_service
from bisheng.open_api.domain.services import service_account_service as svc
from bisheng.user.domain.models.user import User

TABLES = [User.__table__, UserTenant.__table__, ServiceAccount.__table__, ApiCredential.__table__]
TENANT_ID = 41


class _StubPayload:
    def __init__(self, *, global_super: bool = False, tenant_admin: bool = False):
        self.is_global_super = global_super
        self._tenant_admin = tenant_admin

    async def has_tenant_admin(self, tenant_id: int) -> bool:
        return self._tenant_admin


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def aset(self, key, value, expiration=3600):
        self.store[key] = value
        return True

    async def aget(self, key):
        return self.store.get(key)

    async def adelete(self, key):
        self.store.pop(key, None)
        return 1


@pytest.fixture()
async def sa_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: ServiceAccount.metadata.create_all(sync_conn, tables=TABLES))
    yield engine
    await engine.dispose()


@pytest.fixture()
async def patched_db(sa_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=sa_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    monkeypatch.setattr("bisheng.open_api.domain.services.service_account_service.get_async_db_session", _factory)
    yield sa_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(cred_service, "_get_redis", _get)
    # 11806 tenant-status check also consults Redis; share the fake.
    monkeypatch.setattr(deps, "_get_redis", _get)
    # Cache miss triggers a tenant-row check; keep unit tests off the real DB.
    from types import SimpleNamespace

    async def _tenant_active(tenant_id: int):
        return SimpleNamespace(status="active")

    monkeypatch.setattr("bisheng.database.models.tenant.TenantDao.aget_by_id", _tenant_active)
    monkeypatch.setattr(cred_service, "_credential_cache_ttl", lambda: 3)
    return fake


def _stub_payload(monkeypatch, payload):
    async def _init(user_id: int, user_name: str, tenant_id: int | None = None, **kwargs) -> _StubPayload:
        return payload

    monkeypatch.setattr(identity_service.UserPayload, "init_login_user", _init)


async def _count(session_engine, model) -> int:
    async with AsyncSession(bind=session_engine) as session:
        return len((await session.execute(select(model))).all())


# ---------------------------------------------------------------------------
# 创建:一个事务 + 专用身份
# ---------------------------------------------------------------------------


async def test_create_builds_owner_and_membership_in_one_transaction(patched_db, monkeypatch):
    _stub_payload(monkeypatch, _StubPayload())

    account = await svc.create_service_account(tenant_id=TENANT_ID, name="信贷系统", created_by=1)

    assert account.id is not None
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        owner = (
            (await session.execute(select(User).where(User.user_id == account.resource_owner_user_id))).scalars().one()
        )
        membership = (
            (await session.execute(select(UserTenant).where(UserTenant.user_id == owner.user_id))).scalars().one()
        )

    assert owner.user_name.startswith("svc-")
    assert owner.external_id is None and owner.delete == 0
    assert membership.tenant_id == TENANT_ID
    assert membership.is_active == 1 and membership.status == "active"


async def test_owner_name_collision_gets_suffix(patched_db, monkeypatch):
    """专用身份名撞上既有用户时追加后缀(账号名本身有唯一约束,不参与对撞)。"""
    _stub_payload(monkeypatch, _StubPayload())
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(User(user_name="svc-信贷系统", password="x", source="local", external_id=None, delete=0))
        await session.commit()

    a1 = await svc.create_service_account(tenant_id=TENANT_ID, name="信贷系统")
    a2 = await svc.create_service_account(tenant_id=TENANT_ID, name="信贷A")

    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        owner1 = (await session.execute(select(User).where(User.user_id == a1.resource_owner_user_id))).scalars().one()
        owner2 = (await session.execute(select(User).where(User.user_id == a2.resource_owner_user_id))).scalars().one()
    # 既有 'svc-信贷系统' 占位 → 新账号的专用身份拿到 -2;另一个账号名不同,拿基名。
    assert owner1.user_name == "svc-信贷系统-2"
    assert owner2.user_name == "svc-信贷A"


async def test_duplicate_name_is_rejected_cleanly(patched_db, monkeypatch):
    """重名是明确的 11813(含软删重名——名字不复用,design 表1)。"""
    from bisheng.common.errcode.open_api import OpenApiAccountNameDuplicateError

    _stub_payload(monkeypatch, _StubPayload())
    account = await svc.create_service_account(tenant_id=TENANT_ID, name="demo")

    with pytest.raises(OpenApiAccountNameDuplicateError):
        await svc.create_service_account(tenant_id=TENANT_ID, name="demo")

    # 软删后名字仍不可复用
    assert await svc.delete_service_account(account.id, tenant_id=TENANT_ID) is True
    with pytest.raises(OpenApiAccountNameDuplicateError):
        await svc.create_service_account(tenant_id=TENANT_ID, name="demo")


async def test_create_failure_leaves_no_residue(patched_db, monkeypatch):
    """竞态路径:预检通过但库内已有同名(并发写入)→ 唯一约束在 commit 时炸,
    身份/成员/账号三行必须一起回滚,不留孤儿。"""
    _stub_payload(monkeypatch, _StubPayload())
    await svc.create_service_account(tenant_id=TENANT_ID, name="demo")

    before_users = await _count(patched_db, User)

    # 模拟"预检时还没有、提交时已经有了"的并发窗口
    async def _no_dup(name: str, *, tenant_id: int) -> bool:
        return False

    monkeypatch.setattr(
        "bisheng.open_api.domain.services.service_account_service.ServiceAccountDao.aname_exists", _no_dup
    )

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await svc.create_service_account(tenant_id=TENANT_ID, name="demo")

    assert await _count(patched_db, User) == before_users
    assert await _count(patched_db, ServiceAccount) == 1


# ---------------------------------------------------------------------------
# 防提权第一重(11810)与第二重(11816)
# ---------------------------------------------------------------------------


async def test_explicit_admin_owner_rejected(patched_db, monkeypatch):
    _stub_payload(monkeypatch, _StubPayload(global_super=True))
    # 走 11810 的“已提权 owner”分支即可：用户/成员读取打桩，避免本用例
    # 依赖真实 DB（全局 engine 跨 event loop 会污染同进程后续用例）。
    _stub_identity_reads(monkeypatch)

    with pytest.raises(OpenApiOwnerNotAllowedError):
        await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统", resource_owner_user_id=987654)


def _stub_identity_reads(monkeypatch, *, delete: int = 0, status: str = "active"):
    """桩掉 identity_service 的两条读取(真 DAO 会去打真 DB 管理器)。"""

    class _Row:
        pass

    async def _get_user(user_id: int):
        row = User(user_name=f"u{user_id}", password="x", source="local", external_id=None, delete=delete)
        row.user_id = user_id
        return row

    async def _active_tenant(user_id: int):
        row = _Row()
        row.tenant_id = TENANT_ID
        row.status = status
        return row

    monkeypatch.setattr(identity_service.UserDao, "aget_user", _get_user)
    monkeypatch.setattr(identity_service.UserTenantDao, "aget_active_user_tenant", _active_tenant)


async def test_update_owner_change_rejected_for_admin(patched_db, monkeypatch):
    _stub_payload(monkeypatch, _StubPayload())
    account = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")

    _stub_identity_reads(monkeypatch)
    _stub_payload(monkeypatch, _StubPayload(tenant_admin=True))
    with pytest.raises(OpenApiOwnerNotAllowedError):
        await svc.update_service_account(
            account.id, {"resource_owner_user_id": account.resource_owner_user_id}, tenant_id=TENANT_ID
        )


async def test_runtime_privilege_escalation_blocks_calls(patched_db, fake_redis, monkeypatch):
    """创建时正常;owner 事后被提权 → 鉴权依赖运行时拒绝(11816,T-06 x T-17 串联)。"""
    _stub_payload(monkeypatch, _StubPayload())
    account = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")
    _, plaintext = await cred_service.issue_credential(
        tenant_id=TENANT_ID, service_account_id=account.id, scopes=["assistant:read"]
    )
    assert (await validator.validate_bearer(f"Bearer {plaintext}")).id is not None

    # owner 事后被提权 → 依赖装载阶段拒绝(装载本身也桩掉,专注提权判定)
    privileged = _StubPayload(global_super=True)

    async def _load_owner(*, owner_user_id: int, tenant_id: int):
        return privileged

    monkeypatch.setattr(deps, "load_owner_payload", _load_owner)
    monkeypatch.setattr(deps.settings, "open_api", type(deps.settings.open_api)(enforce_keys=True))
    monkeypatch.setattr(tenant_setting_service, "is_enforced", _never_enforced)

    @open_api_scope("assistant:read")
    def _endpoint():
        return None

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v2/x",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {plaintext}".encode())],
        "endpoint": _endpoint,
    }
    with pytest.raises(OpenApiOwnerPrivilegedError):
        await deps.verify_open_api_access(Request(scope)).__anext__()


async def _never_enforced(tenant_id: int) -> bool:
    return False


# ---------------------------------------------------------------------------
# 生命周期:启停与删除
# ---------------------------------------------------------------------------


async def test_disable_revokes_all_keys(patched_db, fake_redis, monkeypatch):
    _stub_payload(monkeypatch, _StubPayload())
    account = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")
    _, key1 = await cred_service.issue_credential(tenant_id=TENANT_ID, service_account_id=account.id, scopes=[])
    _, key2 = await cred_service.issue_credential(tenant_id=TENANT_ID, service_account_id=account.id, scopes=[])

    assert await svc.set_enabled(account.id, enabled=False, tenant_id=TENANT_ID) is True

    for key in (key1, key2):
        with pytest.raises(OpenApiKeyRevokedError):
            await validator.validate_bearer(f"Bearer {key}")

    # 重新启用不回滚密钥状态(吊销是终态),但账号本身恢复
    assert await svc.set_enabled(account.id, enabled=True, tenant_id=TENANT_ID) is True


async def test_delete_disables_owner_and_soft_deletes(patched_db, fake_redis, monkeypatch):
    _stub_payload(monkeypatch, _StubPayload())
    account = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")
    _, key = await cred_service.issue_credential(tenant_id=TENANT_ID, service_account_id=account.id, scopes=[])

    assert await svc.delete_service_account(account.id, tenant_id=TENANT_ID) is True

    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        owner = (
            (await session.execute(select(User).where(User.user_id == account.resource_owner_user_id))).scalars().one()
        )
        membership = (
            (await session.execute(select(UserTenant).where(UserTenant.user_id == owner.user_id))).scalars().one()
        )
        row = (await session.execute(select(ServiceAccount).where(ServiceAccount.id == account.id))).scalars().one()

    assert owner.delete == 1
    assert membership.is_active is None and membership.status == "disabled"
    assert row.deleted_at is not None and row.is_enabled is False

    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {key}")


async def test_cross_tenant_operations_are_invisible(patched_db, monkeypatch):
    """跨租户一律查不到(spec AC-07):启停/删除返回 False,列表为空。"""
    _stub_payload(monkeypatch, _StubPayload())
    account = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")

    assert await svc.set_enabled(account.id, enabled=False, tenant_id=TENANT_ID + 1) is False
    assert await svc.delete_service_account(account.id, tenant_id=TENANT_ID + 1) is False

    from bisheng.open_api.domain.models.dao import ServiceAccountDao

    rows, total = await ServiceAccountDao.alist(tenant_id=TENANT_ID + 1)
    assert rows == [] and total == 0
    rows, total = await ServiceAccountDao.alist(tenant_id=TENANT_ID, keyword="对接")
    assert total == 1 and rows[0].id == account.id
