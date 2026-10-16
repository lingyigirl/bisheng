"""F076 T-26:用户侧保护——服务账号的专用身份不允许在用户侧人工管理。

三件事必须钉住:

1. **识别**:唯一可靠锚点是 `service_account.resource_owner_user_id` 反查(`svc-`
   只是命名约定,人可以撞名);软删账号也计入(名字仍被占用、身份仍可见)。
2. **拒绝**:守卫抛 11821/400,普通用户不受影响(同批里混入身份也整批拒绝)。
3. **接线**:五个用户侧写入入口 + 部门发放默认角色都要真的调用守卫,用户列表要回带
   `is_service_identity`(静态断言,防止有人把守卫摘掉)。
"""

import pathlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from bisheng.common.errcode.open_api import OpenApiIdentityProtectedError
from bisheng.database.models.tenant import UserTenant
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.dao import ServiceAccountDao
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services import identity_service
from bisheng.open_api.domain.services import service_account_service as svc
from bisheng.user.domain.models.user import User

TABLES = [User.__table__, UserTenant.__table__, ServiceAccount.__table__, ApiCredential.__table__]
TENANT_ID = 91
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
async def p_engine():
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
async def patched_db(p_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=p_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    monkeypatch.setattr("bisheng.open_api.domain.services.service_account_service.get_async_db_session", _factory)
    yield p_engine


@pytest.fixture()
async def accounts(patched_db, monkeypatch):
    """两个服务账号(第二个随后软删),外加一个普通人 id。"""

    async def _init(user_id: int, user_name: str, tenant_id: int | None = None, **kwargs):
        class _P:
            is_global_super = False

            async def has_tenant_admin(self, tenant_id: int) -> bool:
                return False

        return _P()

    monkeypatch.setattr(identity_service.UserPayload, "init_login_user", _init)
    first = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统A")
    second = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统B")
    await svc.delete_service_account(second.id, tenant_id=TENANT_ID)
    return {"first": first, "second": second, "human": 999_999}


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------


async def test_async_lookup_finds_service_identities(accounts):
    """列表标注走的异步查询:真 SQL 语义(含软删计入)。"""
    owner_a = accounts["first"].resource_owner_user_id
    owner_b = accounts["second"].resource_owner_user_id

    found = await ServiceAccountDao.aowner_user_ids([owner_a, owner_b, accounts["human"]])
    # 软删账号的身份仍在保护范围内:名字没释放,身份也还在用户列表里。
    assert found == {owner_a, owner_b}
    assert await ServiceAccountDao.aowner_user_ids([]) == set()


async def test_sync_lookup_aggregates_rows(accounts, monkeypatch):
    """同步查询(用户模块端点用)与异步版同条件,只多一层取列/去重。"""

    class _Row:
        def __init__(self, owner_id: int):
            self.resource_owner_user_id = owner_id

    owner = accounts["first"].resource_owner_user_id

    # 空入参在真实现里短路(不查库),桩件保持同样语义。
    def _fake_owners(cls, ids):
        return [] if not list(ids) else [_Row(owner), _Row(owner)]

    monkeypatch.setattr(ServiceAccountDao, "_owners", classmethod(_fake_owners))
    assert ServiceAccountDao.owner_user_ids([owner, accounts["human"]]) == {owner}
    assert ServiceAccountDao.owner_user_ids([]) == set()


# ---------------------------------------------------------------------------
# 拒绝
# ---------------------------------------------------------------------------


async def test_guard_rejects_service_identity(accounts, monkeypatch):
    owner = accounts["first"].resource_owner_user_id
    monkeypatch.setattr(ServiceAccountDao, "owner_user_ids", classmethod(lambda cls, ids: {owner}))
    with pytest.raises(OpenApiIdentityProtectedError) as exc:
        identity_service.assert_not_service_identity([owner])
    assert exc.value.Code == 11821
    assert exc.value.HttpStatus == 400  # 401 会被 console 当成登录失效(design §5 坑 13)


async def test_guard_rejects_whole_batch_when_identity_mixed_in(accounts, monkeypatch):
    owner = accounts["first"].resource_owner_user_id
    monkeypatch.setattr(ServiceAccountDao, "owner_user_ids", classmethod(lambda cls, ids: {owner} if owner in ids else set()))
    with pytest.raises(OpenApiIdentityProtectedError):
        identity_service.assert_not_service_identity([accounts["human"], owner])


async def test_human_owner_is_not_protected(patched_db):
    """被显式指派为 owner 的**真实用户**不受保护——判别标准是"平台创建的身份"
    (`external_id IS NULL`);人类用户一律有 external_id,日常账号操作不受影响。"""
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(
            User(
                user_id=424_242,
                user_name="human-owner",
                password="x",
                source="local",
                external_id="human-owner",  # 有登录身份 ⇒ 是真人账号
            )
        )
        session.add(
            ServiceAccount(
                name="human-owned",
                resource_owner_user_id=424_242,
                tenant_id=TENANT_ID,
            )
        )
        await session.commit()

    assert await ServiceAccountDao.aowner_user_ids([424_242]) == set()


async def test_guard_passes_ordinary_users(accounts, monkeypatch):
    # 普通用户不受影响(本任务的硬约束:仅 svc-* 行为变化)。
    monkeypatch.setattr(ServiceAccountDao, "owner_user_ids", classmethod(lambda cls, ids: set()))
    identity_service.assert_not_service_identity([accounts["human"]])
    identity_service.assert_not_service_identity([])


# ---------------------------------------------------------------------------
# 接线(静态断言:守卫被摘掉就会红)
# ---------------------------------------------------------------------------


def _read(relative: str) -> str:
    return (BACKEND_ROOT / relative).read_text(encoding="utf-8")


def test_every_user_side_write_entry_calls_the_guard():
    """用户 / 用户组 / 组织三条侧面的写入入口全部接线;少一处这条就红。

    计数刻意用"等于"而非"大于":新增入口要同步更新本测试,而不是让它慢慢松动。
    """
    user_api = _read("bisheng/user/api/user.py")
    # 四个入口(改角色 / 启停 / 重置密码 / 自改密码)都在 async 端点里 -> 异步守卫。
    assert user_api.count("await assert_not_service_identity_async(") == 4
    # 旧版用户组(同步端点):换组 + 设组管理员 + 批量设成员(只拦新增)。
    assert _read("bisheng/api/services/role_group_service.py").count("assert_not_service_identity(") == 3
    dept = _read("bisheng/department/domain/services/department_service.py")
    # 加成员 / 设部门管理员 / 保存人员编辑。
    assert dept.count("await assert_not_service_identity_async(") == 3
    # 兜底:部门默认角色发放跳过身份而不是发角色。
    assert "alist_service_identity_ids(user_ids)" in dept
    # F003 用户组:加成员 / 全量同步成员 / 替换某人的组成员关系。
    ug = _read("bisheng/user_group/domain/services/user_group_service.py")
    assert ug.count("await assert_not_service_identity_async(") == 3


def test_user_list_marks_service_identities():
    user_api = _read("bisheng/user/api/user.py")
    assert 'one_data["is_service_identity"] = int(one.user_id) in service_identity_ids' in user_api
    assert "service_identity_ids = await alist_service_identity_ids(" in user_api
