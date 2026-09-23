"""财务报表 OCR JSON 的结构适配工具。"""

from typing import Any

_MARKDOWN_ESCAPE = str.maketrans(
    {
        "|": r"\|",
        "\r": " ",
        "\n": " ",
    }
)


def _normalize_cell(value: Any) -> str:
    """将 OCR 单元格统一转换为 Markdown 表格可用的文本。"""
    if value is None:
        return ""
    return str(value).translate(_MARKDOWN_ESCAPE).strip()


def _normalize_rows(result: dict[str, Any]) -> tuple[list[list[str]], int]:
    """读取 data 中的二维表格, 返回规范化的行和最大列数。"""
    rows = result.get("data") or []
    normalized_rows: list[list[str]] = []
    max_width = len(result.get("column_info") or [])

    if not isinstance(rows, list):
        return normalized_rows, max_width

    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [
            _normalize_cell(cell.get("words", "")) if isinstance(cell, dict) else _normalize_cell(cell) for cell in row
        ]
        max_width = max(max_width, len(cells))
        normalized_rows.append(cells)

    return normalized_rows, max_width


def _normalize_columns(result: dict[str, Any], width: int) -> list[str]:
    """生成与表格列数对齐的表头。"""
    columns = result.get("column_info") or []
    if not isinstance(columns, list):
        columns = []

    normalized = [_normalize_cell(column) for column in columns]
    if len(normalized) < width:
        normalized.extend([""] * (width - len(normalized)))
    elif len(normalized) > width:
        normalized = normalized[:width]

    if not any(normalized):
        normalized = [f"列{index}" for index in range(1, width + 1)]
    return normalized


def financial_result_to_markdown(result: dict[str, Any]) -> str:
    """将一个财务报表 OCR result 转换为 Markdown 表格。"""
    title = _normalize_cell(result.get("description") or "财务报表")
    page_no = result.get("page_no")
    heading = f"## {title}"
    if page_no is not None:
        heading = f"{heading}(第{page_no}页)"

    rows, width = _normalize_rows(result)
    if width <= 0:
        return heading

    columns = _normalize_columns(result, width)
    lines = [
        heading,
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]

    for cells in rows:
        cells.extend([""] * (width - len(cells)))
        if not any(cells):
            continue
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def financial_report_to_markdown(payload: dict[str, Any]) -> str:
    """将财务报表 OCR 的完整响应转换为 Markdown。"""
    data = payload.get("data") or {}
    results = data.get("results") or []
    if not isinstance(results, list):
        return ""

    sections = [financial_result_to_markdown(result) for result in results if isinstance(result, dict)]
    return "\n\n".join(section for section in sections if section.strip())
