from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, delete, text
from sqlmodel import Field, col, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class DeployInstanceBase(SQLModelSerializable):
    node_id: int = Field(index=True)
    template_id: int = Field(index=True)
    name: str = Field(index=True)
    # DB cache of the live container status: running|stopped|error|missing|unknown
    status: str = Field(default="unknown", index=False)
    container_id: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    host_port: int | None = Field(default=None, index=False)
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


class DeployInstance(DeployInstanceBase, table=True):
    __tablename__ = "deploy_instance"

    __table_args__ = (UniqueConstraint("node_id", "name", name="node_name_uniq"),)
    id: int | None = Field(default=None, primary_key=True)


class DeployInstanceCreate(BaseModel):
    node_id: int
    template_id: int
    name: str


class DeployInstanceDao(DeployInstanceBase):
    @classmethod
    async def find_by_id(cls, instance_id: int) -> DeployInstance | None:
        async with get_async_db_session() as session:
            statement = select(DeployInstance).where(DeployInstance.id == instance_id)
            return (await session.exec(statement)).first()

    @classmethod
    async def find_all(cls, node_id: int | None = None) -> list[DeployInstance]:
        async with get_async_db_session() as session:
            statement = select(DeployInstance).order_by(col(DeployInstance.id))
            if node_id is not None:
                statement = statement.where(DeployInstance.node_id == node_id)
            return list((await session.exec(statement)).all())

    @classmethod
    async def find_by_node_and_name(cls, node_id: int, name: str) -> DeployInstance | None:
        async with get_async_db_session() as session:
            statement = select(DeployInstance).where(DeployInstance.node_id == node_id, DeployInstance.name == name)
            return (await session.exec(statement)).first()

    @classmethod
    async def insert(cls, instance: DeployInstance) -> DeployInstance:
        async with get_async_db_session() as session:
            session.add(instance)
            await session.commit()
            await session.refresh(instance)
            return instance

    @classmethod
    async def update(cls, instance: DeployInstance) -> DeployInstance:
        async with get_async_db_session() as session:
            session.add(instance)
            await session.commit()
            await session.refresh(instance)
            return instance

    @classmethod
    async def delete(cls, instance_id: int) -> None:
        statement = delete(DeployInstance).where(col(DeployInstance.id) == instance_id)
        async with get_async_db_session() as session:
            await session.exec(statement)
            await session.commit()

    @classmethod
    async def delete_by_node(cls, node_id: int) -> None:
        statement = delete(DeployInstance).where(col(DeployInstance.node_id) == node_id)
        async with get_async_db_session() as session:
            await session.exec(statement)
            await session.commit()
