from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, Text, delete, text
from sqlmodel import Field, col, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class DeployTemplateBase(SQLModelSerializable):
    name: str = Field(index=True, unique=True)
    image: str = Field(index=False)
    model_path: str = Field(index=False)
    gpu_index: str = Field(default="0", index=False)
    host_port: int = Field(default=8000, index=False)
    container_port: int = Field(default=8000, index=False)
    served_model_name: str = Field(default="", index=False)
    start_args: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_builtin: bool = Field(default=False, index=False)
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


class DeployTemplate(DeployTemplateBase, table=True):
    __tablename__ = "deploy_template"

    id: int | None = Field(default=None, primary_key=True)


class DeployTemplateCreate(DeployTemplateBase):
    pass


class DeployTemplateUpdate(BaseModel):
    name: str | None = None
    image: str | None = None
    model_path: str | None = None
    gpu_index: str | None = None
    host_port: int | None = None
    container_port: int | None = None
    served_model_name: str | None = None
    start_args: str | None = None
    remark: str | None = None


class DeployTemplateDao(DeployTemplateBase):
    @classmethod
    async def find_by_id(cls, template_id: int) -> DeployTemplate | None:
        async with get_async_db_session() as session:
            statement = select(DeployTemplate).where(DeployTemplate.id == template_id)
            return (await session.exec(statement)).first()

    @classmethod
    async def find_all(cls) -> list[DeployTemplate]:
        async with get_async_db_session() as session:
            statement = select(DeployTemplate).order_by(col(DeployTemplate.id))
            return list((await session.exec(statement)).all())

    @classmethod
    async def count(cls) -> int:
        async with get_async_db_session() as session:
            statement = select(DeployTemplate)
            return len((await session.exec(statement)).all())

    @classmethod
    async def insert(cls, template: DeployTemplate) -> DeployTemplate:
        async with get_async_db_session() as session:
            session.add(template)
            await session.commit()
            await session.refresh(template)
            return template

    @classmethod
    async def update(cls, template: DeployTemplate) -> DeployTemplate:
        async with get_async_db_session() as session:
            session.add(template)
            await session.commit()
            await session.refresh(template)
            return template

    @classmethod
    async def delete(cls, template_id: int) -> None:
        statement = delete(DeployTemplate).where(col(DeployTemplate.id) == template_id)
        async with get_async_db_session() as session:
            await session.exec(statement)
            await session.commit()
