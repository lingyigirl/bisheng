from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, Text, delete, text
from sqlmodel import Field, col, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.config.settings import decrypt_token, encrypt_token
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class GpuNodeBase(SQLModelSerializable):
    name: str = Field(index=True)
    host: str = Field(index=True, unique=True)
    port: int = 22
    username: str = "root"
    model_dir: str = "/data1"
    remark: str | None = Field(default=None, index=False)
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), index=True, comment="Tenant ID"),
    )
    create_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    )
    update_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT)
    )


class GpuNode(GpuNodeBase, table=True):
    __tablename__ = "gpu_node"

    id: int | None = Field(default=None, primary_key=True)
    # Fernet-encrypted SSH password; never returned by any API.
    password_encrypted: str = Field(default="", sa_column=Column(Text, nullable=False))

    def decrypt_password(self) -> str:
        return decrypt_token(self.password_encrypted) if self.password_encrypted else ""


class GpuNodeRead(GpuNodeBase):
    id: int | None = None
    has_password: bool = False


class GpuNodeCreate(GpuNodeBase):
    password: str = ""


class GpuNodeUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    model_dir: str | None = None
    remark: str | None = None
    password: str | None = None  # None keeps the stored password


class GpuNodeDao(GpuNodeBase):
    @classmethod
    async def find_by_id(cls, node_id: int) -> GpuNode | None:
        async with get_async_db_session() as session:
            statement = select(GpuNode).where(GpuNode.id == node_id)
            return (await session.exec(statement)).first()

    @classmethod
    async def find_by_host(cls, host: str) -> GpuNode | None:
        async with get_async_db_session() as session:
            statement = select(GpuNode).where(GpuNode.host == host)
            return (await session.exec(statement)).first()

    @classmethod
    async def find_all(cls) -> list[GpuNode]:
        async with get_async_db_session() as session:
            statement = select(GpuNode).order_by(col(GpuNode.id))
            return list((await session.exec(statement)).all())

    @classmethod
    async def insert(cls, node: GpuNode) -> GpuNode:
        async with get_async_db_session() as session:
            session.add(node)
            await session.commit()
            await session.refresh(node)
            return node

    @classmethod
    async def update(cls, node: GpuNode) -> GpuNode:
        async with get_async_db_session() as session:
            session.add(node)
            await session.commit()
            await session.refresh(node)
            return node

    @classmethod
    async def delete(cls, node_id: int) -> None:
        statement = delete(GpuNode).where(col(GpuNode.id) == node_id)
        async with get_async_db_session() as session:
            await session.exec(statement)
            await session.commit()


def to_read(node: GpuNode) -> GpuNodeRead:
    read = GpuNodeRead.model_validate(node)
    read.has_password = bool(node.password_encrypted)
    return read


def encrypt_password(plaintext: str) -> str:
    return encrypt_token(plaintext).decode()
