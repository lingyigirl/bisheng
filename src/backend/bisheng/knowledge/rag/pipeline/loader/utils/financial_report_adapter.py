"""财务报表 OCR JSON 的结构适配工具。"""

import re
from dataclasses import dataclass
from typing import Any

_MARKDOWN_ESCAPE = str.maketrans(
    {
        "|": r"\|",
        "\r": " ",
        "\n": " ",
    }
)


@dataclass(frozen=True)
class BBoxPartition:
    """Markdown 里一个可定位片段的字符区间与坐标。

    ``bbox`` 仍是 OCR 的原生（栅格像素）坐标系 —— 换算成 PDF point 是 loader 的事，
    适配层保持纯函数、不碰文件。
    """

    start: int  # 在 markdown 里的起始偏移
    end: int  # 含端偏移，与 splitter 查询 chunk 区间时的约定一致
    page: int  # 1 基
    bbox: list[int]  # [x1, y1, x2, y2]
    type: str  # 'text'（页面级文字）/ 'table'（表格单元格）
    text: str  # 对应的 markdown 片段


def _normalize_cell(value: Any) -> str:
    """将 OCR 单元格统一转换为 Markdown 表格可用的文本。"""
    if value is None:
        return ""
    return str(value).translate(_MARKDOWN_ESCAPE).strip()


def _coerce_bbox(value: Any) -> list[int] | None:
    """取前 4 个坐标并截断成整数，形状不对时返回 None。

    必须是整数：MinIO 的 key 由 ``extra_file.py`` 用 ``str(int(one))`` 拼出，
    前端平台预览页再用 ``[page, ...bbox].join('-')`` 反查同一个 key ——
    留浮点值会对不上 key，高亮直接失效。用 ``int()`` 而非 ``round()``，
    与拼 key 时的取整方式保持一致。
    """
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        return [int(one) for one in value[:4]]
    except (TypeError, ValueError):
        return None


def _cells_to_grid(cells: Any) -> tuple[list[list[str]], list[list[list[int] | None]], int]:
    """将 /ocr/v1/table_multi 的扁平 cells 还原为二维表格，并保留每格的 bbox。

    该接口不返回二维数组，而是给出带 ``row_index`` / ``col_index`` 的扁平格列表。
    跨行/跨列（``rows_span`` / ``cols_span`` > 1）只在锚点格填值，被覆盖的格子留空 ——
    Markdown 表格表达不了合并单元格，留空比重复值更接近原表的视觉结构；
    这些空格也没有 bbox 可归属（框属于锚点格）。

    返回 ``(文本网格, bbox 网格, 列数)``，两张网格同形、同宽。
    """
    if not isinstance(cells, list):
        return [], [], 0

    text_grid: dict[tuple[int, int], str] = {}
    bbox_grid: dict[tuple[int, int], list[int] | None] = {}
    width = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        row_index = cell.get("row_index")
        col_index = cell.get("col_index")
        if not isinstance(row_index, int) or not isinstance(col_index, int):
            continue
        if row_index < 0 or col_index < 0:
            continue
        col_span = cell.get("cols_span")
        col_span = col_span if isinstance(col_span, int) and col_span > 1 else 1
        text_grid[(row_index, col_index)] = _normalize_cell(cell.get("words", ""))
        bbox_grid[(row_index, col_index)] = _coerce_bbox(cell.get("bbox"))
        # 跨列必须算进宽度，否则最后一列会被截断
        width = max(width, col_index + col_span)

    if not text_grid:
        return [], [], 0
    height = max(row for row, _ in text_grid) + 1
    rows = [[text_grid.get((row, col), "") for col in range(width)] for row in range(height)]
    bboxes = [[bbox_grid.get((row, col)) for col in range(width)] for row in range(height)]
    return rows, bboxes, width


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


#: 中日韩文字。页面级文字靠它区分「有内容的短句」和「OCR 碎片」——
#: ``国名：`` 只有 3 字符但是有效信息，``1V1`` 同样 3 字符却只是碎片。
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
#: 不含中日韩文字时的最小长度，短于此判为碎片。
_MIN_TEXT_LEN = 4


def _is_noise(words: str) -> bool:
    """判断页面级文字条目是否为 OCR 碎片。

    判据是「单字符」或「不含中日韩文字且长度 < 4」。刻意不用 ``score`` 做判据：
    实测碎片置信度 0.83~0.97、有效信息 0.91~0.99，两个区间重叠，阈值只会误杀。
    """
    return len(words) <= 1 or (len(words) < _MIN_TEXT_LEN and not _CJK_RE.search(words))


def ocr_page_number(page: dict[str, Any]) -> int:
    """OCR 的 ``page_no`` 是 0 基，这里统一转成 1 基。

    链路两端都用 1 基：前端平台预览页写死 ``(page - 1) * paperSize`` 做纵向定位，
    ``TextBbox.page`` 的默认值也是 1，MinIO 里 bbox 的 key 同样以它为前缀。
    """
    page_no = page.get("page_no")
    return page_no + 1 if isinstance(page_no, int) and page_no >= 0 else 1


def _position_bbox(position: dict[str, Any]) -> list[int] | None:
    """页面级文字的 ``position`` 矩形 → ``[x1, y1, x2, y2]``；字段不齐或非数值返回 None。"""
    try:
        left = int(position["left"])
        top = int(position["top"])
        right = left + int(position["width"])
        bottom = top + int(position["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return [left, top, right, bottom]


def _page_text_pieces(page: dict[str, Any]) -> list[tuple[str, list[int] | None]]:
    """取页面级 ``text`` 里可用的条目，返回 ``[(文本, bbox 或 None), ...]``（按阅读顺序）。

    这些文字不属于任何 ``cell``，是单据标题、开票人、发票类型这类只有这里能拿到的
    信息，丢掉就没有可召回的文本。按 ``(top, left)`` 排序还原阅读顺序；``position``
    缺失或非法的条目退化为 0 排在页首，且因取不到矩形而不带 bbox（文本照样保留）。
    """
    items = [item for item in (page.get("text") or []) if isinstance(item, dict)]

    kept: list[tuple[int, int, str, list[int] | None]] = []
    for item in items:
        # 复用 _normalize_cell 转义 ``|``、压掉换行，避免页级文字破坏 Markdown 表格结构
        words = _normalize_cell(item.get("words", ""))
        if not words or _is_noise(words):
            continue
        position = item.get("position")
        position = position if isinstance(position, dict) else {}
        top, left = position.get("top"), position.get("left")
        kept.append(
            (
                top if isinstance(top, int) else 0,
                left if isinstance(left, int) else 0,
                words,
                _position_bbox(position),
            )
        )

    kept.sort(key=lambda row: (row[0], row[1]))
    return [(words, bbox) for _, _, words, bbox in kept]


def _render_row(cells: list[str], bboxes: list[list[int] | None]) -> tuple[str, list[tuple[int, int, list[int]]]]:
    """把一行单元格渲染成 ``| a | b |``，并给出带 bbox 的格子在行内的含端偏移。

    逐格拼接而不是 ``" | ".join(cells)``：偏移必须与最终文本严格对应，
    靠字符串反查在内容重复的表头上必然错位（样例里两张表产出的是同一个合成表头）。
    """
    text = "|"
    spans: list[tuple[int, int, list[int]]] = []
    for cell_text, bbox in zip(cells, bboxes, strict=False):
        start = len(text) + 1  # 跳过 "|" 后面那个空格
        text += f" {cell_text} |"
        if bbox is not None and cell_text:
            spans.append((start, start + len(cell_text) - 1, bbox))
    return text, spans


def _render_table(table: dict[str, Any]) -> tuple[str, list[tuple[int, int, list[int]]]] | None:
    """渲染一张表，返回 ``(Markdown 文本, [(起, 含端止, bbox), ...])``；空表返回 None。

    每个**单元格**一个分区。合成表头（该接口响应里根本没有列名字段）、分隔线、
    被合并单元格覆盖的空格都没有 bbox 来源，只占文本、不产出分区。
    """
    rows, bboxes, width = _cells_to_grid(table.get("cells"))
    # 空表或全空单元格：不产出「只有表头 + 分隔线」的噪声块
    if width <= 0 or not any(any(row) for row in rows):
        return None

    columns = _normalize_columns({}, width)  # column_info 恒为空 → 兜底成 列1..列N
    lines: list[tuple[str, list[tuple[int, int, list[int]]]]] = [
        ("## 表格", []),
        ("", []),
        ("| " + " | ".join(columns) + " |", []),
        ("|" + "|".join(["---"] * width) + "|", []),
    ]
    for cells, cell_bboxes in zip(rows, bboxes, strict=False):
        if not any(cells):
            continue
        lines.append(_render_row(cells, cell_bboxes))

    text = "\n".join(line for line, _ in lines)
    spans: list[tuple[int, int, list[int]]] = []
    offset = 0
    for line, line_spans in lines:
        spans.extend((offset + start, offset + end, bbox) for start, end, bbox in line_spans)
        offset += len(line) + 1  # 末尾那个 "\n"；最后一行的 +1 不影响任何偏移
    return text, spans


def _table_multi_markdown(payload: dict[str, Any]) -> tuple[str, list[BBoxPartition]]:
    """渲染 /ocr/v1/table_multi 响应，同时记下每个可定位片段的字符区间。

    每页先输出页面级文字、再输出该页表格，切分后首个 chunk 才能同时拿到单据标题和
    表头。页面级文字只滤掉 OCR 碎片（见 :func:`_is_noise`），其余全部保留：它们与
    表格 ``cell`` 内容不重复（真实响应逐条比对验证过），且是表格里拿不到的信息。
    """
    results = payload.get("results")
    if not isinstance(results, list):
        return "", []

    buf: list[str] = []
    partitions: list[BBoxPartition] = []
    length = 0

    def append(text: str) -> int:
        """追加文本，返回它的起始偏移。"""
        nonlocal length
        start = length
        buf.append(text)
        length += len(text)
        return start

    def start_section() -> None:
        """区块之间统一空一行，与 ``"\\n\\n".join`` 等价。"""
        if length:
            append("\n\n")

    def append_partition(text: str, page: int, bbox: list[int] | None, kind: str) -> None:
        start = append(text)
        if text and bbox is not None:
            partitions.append(BBoxPartition(start, start + len(text) - 1, page, bbox, kind, text))

    for result in results:
        if not isinstance(result, dict):
            continue
        page = ocr_page_number(result)

        # 必须放在 tables 类型检查之前：只有文字、没有表格的页也要产出内容
        pieces = _page_text_pieces(result)
        if pieces:
            start_section()
            for index, (words, bbox) in enumerate(pieces):
                if index:
                    append("\n")
                append_partition(words, page, bbox, "text")

        tables = result.get("tables")
        if not isinstance(tables, list):
            continue
        for table in tables:
            if not isinstance(table, dict):
                continue
            rendered = _render_table(table)
            if rendered is None:
                continue
            start_section()
            table_text, spans = rendered
            base = append(table_text)
            for start, end, bbox in spans:
                partitions.append(
                    BBoxPartition(base + start, base + end, page, bbox, "table", table_text[start : end + 1])
                )

    return "".join(buf), partitions


def build_markdown(payload: dict[str, Any]) -> tuple[str, list[BBoxPartition]]:
    """将 OCR 响应渲染成 Markdown，并给出每个可定位片段的字符区间与坐标。

    适配层的唯一入口。旧形状（``data.results``）没有几何信息，只返回文本、分区为空。
    """
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return _table_multi_markdown(payload)

    sections = [financial_result_to_markdown(result) for result in data["results"] if isinstance(result, dict)]
    return "\n\n".join(section for section in sections if section.strip()), []


def financial_report_to_markdown(payload: dict[str, Any]) -> str:
    """将财务报表 OCR 的完整响应转换为 Markdown。

    **旧形状优先**：``data.results`` 存在时按财务报表形状渲染，否则回落到
    ``/ocr/v1/table_multi`` 的顶层 ``results`` 形状。顺序不能颠倒 —— 旧形状优先时，
    新分支不可能劫持任何已上线的响应。
    """
    return build_markdown(payload)[0]
