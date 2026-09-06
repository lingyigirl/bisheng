from typing import Any

from .base import BaseMidTable, BaseRecord


class DatasetIncrementRecord(BaseRecord):
    dataset_id: int


class DatasetIncrement(BaseMidTable):
    _index_name: str = "mid_dataset_increment"
    _mappings: dict[str, Any] = {}
