"""F076 开放 API 的 `tools[]` 请求形态(T-18,INV-24)。

与平台既有的"按请求选工具"先例**同形**——日间模式的
`bisheng/api/v1/schema/chat_schema.py:44-57 ToolPayload{id, tool_key, type}`:
同一平台里两套 `tools[]` 语义会让人踩坑,所以这里刻意对齐,而不是另造一套。

语义(design 决策 6 / spec AC-29):**白名单**——列出即"本轮启用",未列出的一律
不启用;不传 `tools[]` 时行为与升级前完全一致(按应用默认配置执行)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from bisheng.common.errcode.open_api import OpenApiToolNotAllowedError


class OpenApiToolPayload(BaseModel):
    """一条工具选择项;`id` 与 `tool_key` 至少给一个(优先按 `tool_key` 匹配)。"""

    id: int = 0
    tool_key: str | None = None
    type: str = Field(default="tool", description="与日间模式对齐;当前仅 'tool'")


def parse_tool_selection(raw: list | None) -> list[OpenApiToolPayload] | None:
    """解析调用方传来的 `tools` 原始值。

    - `None` / 空列表 → 返回 `None`,调用方按"未携带"处理(行为不变);
    - 形状非法(不是对象、类型不对)→ 11815(INV-24:不可识别必须显式报错,
      不得静默忽略)。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise OpenApiToolNotAllowedError(**{"tool_key": None, "reason": "tools must be a list"})
    if not raw:
        return None

    parsed: list[OpenApiToolPayload] = []
    for item in raw:
        try:
            parsed.append(OpenApiToolPayload.model_validate(item))
        except ValidationError as exc:
            raise OpenApiToolNotAllowedError(**{"tool_key": None, "reason": str(exc.errors()[:1])}) from exc
    return parsed
