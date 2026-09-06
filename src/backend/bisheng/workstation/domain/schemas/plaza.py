"""F047 agent plaza — request schemas."""
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable


class FavoriteBody(SQLModelSerializable):
    """Favorite / unfavorite request body."""

    app_id: str = Field(max_length=64, description='App (flow) id — int for assistants, UUID for workflows')
    app_type: int = Field(description='FlowType value: assistant=5 / workflow=10')
