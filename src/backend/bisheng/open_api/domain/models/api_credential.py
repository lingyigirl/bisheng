"""F076 API 密钥表(`api_credential`)。

密钥形如 `bs-sak-<secrets.token_urlsafe(32)>`(43 字符)。**校验**只依赖
SHA-256 摘要(`token_hash` 唯一);为支持管理员显式查看,另存一份 Fernet
密文(`token_ciphertext`,可空)。明文不出现在日志 / 审计 / 列表 / 缓存 /
异常中;密文只在 reveal 动作里解密并留审计(INV-23 条款 1,spec AC-08 / AC-09)。

`allowed_apps` 是**每把密钥**的智能体白名单:`NULL` = 不限制(沿用账号资源
授权),`[]` = 不调用任何智能体,非空 = 只允许列表内的助手 / 工作流 / 纳管
智能体。它只收窄、不扩权(INV-27,spec AC-42)。

有效性是**单一判据**:未撤销且未过期(`is_valid_at`,spec AC-12);
吊销 ≠ 删除——记录保留供审计(spec AC-11)。同账号允许多把并存用于轮换(spec AC-10)。

租户感知:登记在 core/database/tenant_filter.py 的 `_TENANT_AWARE_MODEL_MODULES`;
`tenant_id` 必须 `default=None`。匿名校验密钥时以 `bypass_tenant_filter()` 跨租户读取。

迁移:整表由 SQLModel `create_all` 建;`token_ciphertext` / `allowed_apps`
两列由 Alembic revision `f076_api_key_reveal_allowlist` 追加(2026-10-22)。
回滚时**不要** drop `allowed_apps`(会把权限放宽回账号级),只回退代码。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType

#: 服务账号密钥前缀;与主体类型绑死,未知前缀一律拒绝(design §3 决策 5)。
SERVICE_ACCOUNT_KEY_PREFIX = "bs-sak-"
#: `secrets.token_urlsafe(32)` 的定长输出——签发时断言该长度(design §4.2)。
KEY_SECRET_LENGTH = 43
#: 掩码填充;展示形如 `bs-sak-********AbCd`(只允许回显末四位)。
KEY_MASK_FILL = "********"

REVOKE_REASON_MANUAL = "manual"
REVOKE_REASON_BATCH = "batch"
REVOKE_REASON_REISSUED = "reissued"
REVOKE_REASON_SUBJECT_DISABLED = "subject_disabled"
REVOKE_REASON_SUBJECT_DELETED = "subject_deleted"
REVOKE_REASONS = frozenset(
    {
        REVOKE_REASON_MANUAL,
        REVOKE_REASON_BATCH,
        REVOKE_REASON_REISSUED,
        REVOKE_REASON_SUBJECT_DISABLED,
        REVOKE_REASON_SUBJECT_DELETED,
    }
)


class ApiCredential(SQLModelSerializable, table=True):
    """One API key belonging to a service account (spec AC-08 ~ AC-15).

    The plaintext is never persisted: `token_hash` is a SHA-256 hex digest and
    `last4` exists only so the management list can show a mask. `scopes` is a
    JSON array of scope codes from `open_api.domain.scopes`.
    """

    __tablename__ = "api_credential"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_api_credential_token_hash"),
        Index("idx_api_credential_account", "tenant_id", "service_account_id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    service_account_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment="所属服务账号ID"),
    )
    name: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, comment="密钥名(轮换与对账用)"),
    )
    key_prefix: str = Field(
        sa_column=Column(String(16), nullable=False, server_default=text("'bs-sak-'"), comment="密钥前缀"),
    )
    last4: str = Field(
        sa_column=Column(String(4), nullable=False, comment="明文末四位(仅用于掩码展示)"),
    )
    token_hash: str = Field(
        sa_column=Column(String(64), nullable=False, comment="SHA-256(明文) 十六进制摘要;校验唯一依据"),
    )
    token_ciphertext: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        sa_column=Column(
            String(512),
            nullable=True,
            comment="Fernet 密文(仅管理员显式查看时解密);NULL=存量密钥不可查看",
        ),
    )
    scopes: list = Field(
        default_factory=list,
        sa_column=Column(JsonType, nullable=False, comment="权限位数组(open_api.domain.scopes 的 code)"),
    )
    allowed_apps: list | None = Field(
        default=None,
        sa_column=Column(
            JsonType,
            nullable=True,
            comment="每 Key 智能体白名单 [{type,id}];NULL=不限制,[]=全部拒绝",
        ),
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="有效期;NULL=不过期;过期判定为排他(> now 才有效)"),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="吊销时间;吊销 ≠ 删除(保留审计归属)"),
    )
    revoke_reason: str | None = Field(
        default=None,
        sa_column=Column(
            String(32), nullable=True, comment="吊销原因: manual/batch/reissued/subject_disabled/subject_deleted"
        ),
    )
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="最后使用时间(best-effort,允许分钟级延迟)"),
    )
    created_by: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="签发人(管理员)ID"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )

    @property
    def key_mask(self) -> str:
        """回显用掩码:前缀 + 填充 + 末四位(spec AC-08)。"""
        return f"{self.key_prefix}{KEY_MASK_FILL}{self.last4}"

    def is_valid_at(self, now: datetime) -> bool:
        """单一有效性判据:未吊销且未过期(过期排他,spec AC-12)。"""
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now
