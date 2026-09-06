"""F046: call-log query parameters."""

import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7


class CallLogQuery(BaseModel):
    record_type: Literal["agent_run", "model_call"] | None = Field(
        default=None, description="Record type filter; empty returns both"
    )
    app_id: str | None = None
    status: Literal["success", "failed"] | None = None
    start_time: int | None = Field(default=None, description="Epoch seconds, inclusive")
    end_time: int | None = Field(default=None, description="Epoch seconds, exclusive")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    def resolve_window(self) -> tuple[int, int]:
        """Resolve the time window with the 90-day cap (spec AC boundary)."""
        end = self.end_time if self.end_time is not None else int(time.time())
        start = self.start_time if self.start_time is not None else end - DEFAULT_RANGE_DAYS * 86400
        if end - start > MAX_RANGE_DAYS * 86400:
            raise HTTPException(status_code=400, detail="time range exceeds 90 days")
        if start >= end:
            raise HTTPException(status_code=400, detail="start_time must be before end_time")
        return start, end
