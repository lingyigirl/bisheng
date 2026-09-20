"""The citation anchor a database-backed answer carries.

Persisted with the message so the expand panel still works after a reload or on
another device (spec AC-34). That is why every field here is JSON-safe and why
nothing is fetched lazily -- a datasource can be deleted between the answer and
the reload, and an anchor that needed a live lookup would render as a hole
(spec AC-62 keeps the history readable).

`rows` is the result table the user expands, bounded by the executor's row cap
(AC-35). It is NOT re-trimmed here: a second trimming rule would eventually
disagree with the first, and the user would see a different table depending on
which path rendered it.

**This payload is permission-bearing.** It names the datasource and its tables,
so the citation resolve path must filter it like a RAG citation rather than
passing it through like a web link -- see `CitationType.DATASOURCE` and
`AUTHORIZED_TYPES`.
"""

# ruff: noqa: RUF001 -- the field descriptions are Chinese text shown in the UI;
# fullwidth punctuation is correct in them.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DatasourceCitationPayloadSchema(BaseModel):
    """One database query behind one answer, in the shape the UI renders."""

    datasourceId: int = Field(..., description='数据源ID')
    datasourceName: str = Field(..., description='数据源名称，展示用')
    #: The tables the statement touched. A summary for the reader, not the data.
    tables: list[str] = Field(default_factory=list, description='本次查询涉及的表')
    #: The statement that ran. This is what makes the answer auditable -- a
    #: reader can judge whether the question was answered by the right query.
    statement: str = Field(default='', description='本次执行的语句')
    columns: list[str] = Field(default_factory=list, description='结果列名')
    rows: list[list[Any]] = Field(default_factory=list, description='结果表（受执行期行数上限约束）')
    truncated: bool = Field(default=False, description='结果是否因超行数上限被截断')

    class Config:
        # Extra keys are kept rather than dropped: a payload written by a newer
        # process must survive a round trip through an older one during a
        # rolling deploy, or the anchor would lose fields on reload.
        extra = 'allow'
