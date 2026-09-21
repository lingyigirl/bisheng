"""F060 T-05: the two importers share their file-reading layer.

The contract requires F060 to reuse F059's import parsing rather than write a
second one, and design decision 8 narrowed what "reuse" can mean here: the term
library reads three columns while the word library treats every cell as a token,
so the shared part is the CONTAINER handling — which extensions, how to decode,
how to read a workbook, how to spot a header row. Column semantics stay with each
feature.

That makes the interesting assertions about the boundary rather than either side:
that one implementation really is doing the reading (not two that happen to agree
today), and that the failure the contract names — "the same file parses in one
place but not the other" — cannot happen.

`test/sensitive_word/` holds F059's side of this; the extraction's main guardrail
was that its whole suite stayed green.

Scope note: this file covers the SHARED LAYER and F059's use of it. The
cross-importer "same file, same verdict" assertions need both importers to
exist, so they land with T-09 (`test_term_importer.py`) rather than here.
"""

import io

import pytest

from bisheng.sensitive_word.domain.services import platform_word_service, tabular_import


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    """A real workbook, so the xlsx path is exercised rather than stubbed."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ── the sharing itself ───────────────────────────────────────────────────────


def test_the_word_service_reads_through_the_shared_layer():
    """Not "behaviour matches" but "the same code runs".

    Two implementations that agree today drift the first time one is fixed — and
    the contract's whole point was that there should not be two. Asserted by
    patching the shared reader and checking the word service observes it: if the
    service still did its own reading, this would have no effect.
    """
    calls = []

    def _spy(raw, filename, **kwargs):
        calls.append((raw, filename))
        return [['stub']]

    original = tabular_import.read_rows
    platform_word_service.read_rows = _spy
    try:
        parsed = platform_word_service.PlatformWordService.parse_words_file(
            b'x', 'words.csv'
        )
    finally:
        platform_word_service.read_rows = original

    assert calls, (
        'parse_words_file did not go through tabular_import.read_rows -- the two '
        'importers are reading files independently again'
    )
    # The word library reads every cell as a word, which is its own semantics.
    assert parsed.words == ['stub']


# ── the shared layer's own behaviour ─────────────────────────────────────────


def test_txt_is_readable_by_the_shared_layer_but_not_accepted_for_terms():
    """`txt` stays in the shared extension list because the word library accepts
    it; the term library narrows it out (design decision 8). Asserted as the
    asymmetry itself, since that is the arrangement — not an inconsistency.
    """
    assert 'txt' in tabular_import.SUPPORTED_EXTENSIONS
    assert tabular_import.read_text(b'a\tb') == [['a', 'b']]

    with pytest.raises(tabular_import.TabularImportError):
        tabular_import.read_rows(b'a', 'words.txt', allowed=('csv', 'xlsx'))


def test_a_utf16_file_without_a_bom_is_rejected_not_silently_imported():
    """The trap the shared layer exists to keep in one place: UTF-16 without a BOM
    decodes "successfully" as UTF-8 into NUL-laden values that can never match
    anything. Importing them would report success and pollute the library.
    """
    with pytest.raises(tabular_import.TabularImportError):
        tabular_import.read_text('hello'.encode('utf-16-le'))


def test_xlsx_is_read_from_the_active_sheet():
    raw = _xlsx_bytes([['标准词', '释义'], ['不良贷款率', '不良贷款占贷款总额的比例']])

    rows = tabular_import.read_xlsx(raw)

    assert rows[0] == ['标准词', '释义']
    assert rows[1][0] == '不良贷款率'


def test_a_none_cell_becomes_an_empty_string_not_the_word_none():
    """An empty cell is common in a real sheet; `str(None)` would import the
    literal text "None" as a term."""
    raw = _xlsx_bytes([['a', None, 'c']])

    rows = tabular_import.read_xlsx(raw)

    assert rows[0] == ['a', '', 'c']
    assert 'None' not in rows[0]
