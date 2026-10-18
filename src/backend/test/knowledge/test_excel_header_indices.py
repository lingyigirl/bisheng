"""Keep API row numbers and CSV/XLSX Markdown headers aligned."""

from types import SimpleNamespace

import openpyxl
import pytest

from bisheng.api.v1.schemas import ExcelRule
from bisheng.knowledge.rag.base_file_pipeline import BaseFilePipeline
from bisheng.knowledge.rag.pipeline.loader.utils.md_from_excel import convert_file_to_markdown


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
@pytest.mark.parametrize("start,end,expected", [(1, 1, [0, 0]), (1, 2, [0, 1]), (2, 2, [1, 1])])
def test_api_header_rows_are_converted_once_before_markdown(tmp_path, extension, start, end, expected):
    rule = ExcelRule(header_start_row=start, header_end_row=end)
    pipeline = SimpleNamespace(file_split_rule=SimpleNamespace(excel_rule=rule))
    indices = BaseFilePipeline._excel_header_indices(pipeline)
    assert indices == expected
    rows = [["date", "amount"], ["2026-03-02", "-20"], ["2026-01-05", "0"]]
    source = tmp_path / f"input.{extension}"
    if extension == "csv":
        source.write_text("\n".join(",".join(row) for row in rows), encoding="utf-8-sig")
    else:
        workbook = openpyxl.Workbook()
        for row in rows:
            workbook.active.append(row)
        workbook.save(source)
        workbook.close()
    output = tmp_path / "md"
    output.mkdir()
    convert_file_to_markdown(str(source), indices, 10, str(output), append_header=True)
    documents = list(output.glob("*.md"))
    assert documents
    text = documents[0].read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith("|")]
    assert rows[start - 1][0] in lines[0]
    assert sum("2026-03-02" in line for line in lines) == 1
    assert sum("date" in line for line in lines) == 1
