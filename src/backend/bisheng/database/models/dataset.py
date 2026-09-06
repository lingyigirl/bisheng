from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, delete, text
from sqlmodel import Field, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_sync_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class DatasetBase(SQLModelSerializable):
    user_id: int | None = Field(index=True, description="Create Userid")
    name: str = Field(index=True, description="Dataset Name")
    type: str = Field(index=False, default=0, description="Reserved Fields")
    description: str | None = Field(default=None, index=False, description="Dataset description")
    object_name: str | None = Field(default=None, index=False, description="data setS3Part Name")
    create_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    )
    update_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT)
    )


class Dataset(DatasetBase, table=True):
    id: int | None = Field(default=None, primary_key=True)


class DatasetCreate(DatasetBase):
    pass


class DatasetRead(DatasetBase):
    user_name: str | None
    url: str | None


class DatasetUpdate(DatasetBase):
    pass


class DatasetDao(DatasetBase):
    @classmethod
    def get_datasets_by_create_window(
        cls,
        start_time: datetime,
        end_time: datetime,
        page: int = 1,
        page_size: int = 1000,
    ) -> list[Dataset]:
        """Datasets created inside [start_time, end_time), paginated by id."""
        with get_sync_db_session() as session:
            query = (
                select(Dataset)
                .where(Dataset.create_time >= start_time, Dataset.create_time < end_time)
                .order_by(Dataset.id)
            )
            if page and page_size:
                query = query.offset((page - 1) * page_size).limit(page_size)
            return session.exec(query).all()

    @classmethod
    def filter_dataset_by_ids(
        cls, dataset_ids: list[int], keyword: str = None, page: int = 0, limit: int = 0
    ) -> list[Dataset]:
        with get_sync_db_session() as session:
            query = select(Dataset)
            if dataset_ids:
                query = query.where(Dataset.id.in_(dataset_ids))
            if keyword:
                query = query.where(Dataset.name.like(f"%{keyword}%"))
            if page and limit:
                query = query.order_by(Dataset.update_time.desc()).offset((page - 1) * limit).limit(limit)

            return session.exec(query).all()

    @classmethod
    def get_count_by_filter(cls, filters: list[Any]) -> int:
        with get_sync_db_session() as session:
            return session.scalar(select(Dataset.id).where(*filters))

    @classmethod
    def insert(cls, dataset: DatasetCreate):
        with get_sync_db_session() as session:
            db_insert = Dataset.validate(dataset)
            session.add(db_insert)
            session.commit()
            session.refresh(db_insert)
            return db_insert

    @classmethod
    def get_dataset_by_name(cls, name: str):
        with get_sync_db_session() as session:
            return session.exec(select(Dataset).where(Dataset.name == name)).all()

    @classmethod
    def update(cls, data: Dataset):
        with get_sync_db_session() as session:
            session.add(data)
            session.commit()
            session.refresh(data)
            return data

    @classmethod
    def delete(cls, dataset_id: int):
        with get_sync_db_session() as session:
            session.exec(delete(Dataset).where(Dataset.id == dataset_id))
            session.commit()
