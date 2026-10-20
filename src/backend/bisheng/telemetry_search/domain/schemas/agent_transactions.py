"""F052: agent-transaction monitor query schemas (transaction list / node detail)."""

import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7

# 6.3: feedback-based filtering for the fine-tuning dataset export.
ExportFeedback = Literal["all", "liked", "exclude_disliked"]


class AgentTransactionQuery(BaseModel):
    """List-query parameters; window semantics match F046 call-logs (spec AC)."""

    app_id: str | None = None
    app_type: str | None = None
    status: str | None = None
    keyword: str | None = None
    start_time: int | None = None
    end_time: int | None = None
    page: int = 1
    page_size: int = 20

    def resolve_window(self) -> tuple[int, int]:
        """Resolve the time window with the 90-day cap (spec AC 边界)."""
        end = self.end_time if self.end_time is not None else int(time.time())
        start = self.start_time if self.start_time is not None else end - DEFAULT_RANGE_DAYS * 86400
        if end - start > MAX_RANGE_DAYS * 86400:
            raise HTTPException(status_code=400, detail="time range exceeds 90 days")
        if start >= end:
            raise HTTPException(status_code=400, detail="start_time must be before end_time")
        return start, end


class AgentTransactionExportQuery(AgentTransactionQuery):
    """6.3: run-list filters plus the fine-tuning export options.

    ``limit`` is a safety cap, not a page size: exceeding it is a hard error so
    the caller narrows the time range instead of silently receiving a partial
    dataset. ``exclude_disliked`` is the default because unrated runs are still
    useful (production traffic is mostly unrated), while an explicit dislike is
    the one signal we must not feed back into training.
    """

    feedback: ExportFeedback = "exclude_disliked"
    limit: int = Field(default=5000, ge=1, le=5000)
