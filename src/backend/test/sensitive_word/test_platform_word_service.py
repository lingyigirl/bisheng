"""T006 (F059): import parsing + diff computation for the platform word library.

These cover the PURE functions — no database, no network. Everything here is
about turning a byte blob a super admin uploaded into a de-duplicated list of
words, and about computing the preview numbers shown before an overwrite is
confirmed (spec AC-02..AC-05).
"""

import io

import pytest

from bisheng.common.errcode.platform_sensitive_word import PlatformWordFileParseError
from bisheng.sensitive_word.domain.services.platform_word_service import (
    ImportMode,
    PlatformWordService,
)


def _parse(content: str, filename: str = 'words.txt'):
    return PlatformWordService.parse_words_file(content.encode('utf-8'), filename)


def _txt(content: str) -> bytes:
    return content.encode('utf-8')


# ---------------------------------------------------------------------------
# Splitting + de-duplication
# ---------------------------------------------------------------------------


def test_splits_on_mixed_separators():
    """All supported separators work in one file, half- and full-width alike."""
    assert _parse('alpha,beta，gamma\ndelta;epsilon；zeta|eta').words == [  # noqa: RUF001
        'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta',
    ]


def test_ignores_blank_lines_and_strips_whitespace():
    assert _parse('  alpha  \n\n\n  ,  beta  ,\n').words == ['alpha', 'beta']


def test_dedupes_repeated_words_keeping_first_occurrence_order():
    assert _parse('b,a,b,c,a').words == ['b', 'a', 'c']


def test_mixed_width_punctuation_does_not_produce_duplicates():
    """`alpha,alpha` and `alpha，alpha` are the same word, not two."""  # noqa: RUF002
    assert _parse('alpha,alpha，alpha;alpha|alpha').words == ['alpha']  # noqa: RUF001


def test_parse_reports_how_many_tokens_were_dropped_as_duplicates():
    """The preview's `duplicates` figure has to come from here — the parser is
    the only place that sees the raw token stream."""
    parsed = _parse('a,b,a,c,b,a')
    assert parsed.words == ['a', 'b', 'c']
    assert parsed.duplicates == 3


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_csv_header_row_is_ignored():
    """A single-column csv whose first row is a header must not import the header."""
    assert _parse('word\nalpha\nbeta\n', 'words.csv').words == ['alpha', 'beta']


def test_csv_header_with_multiple_columns_keeps_all_cells_of_data_rows():
    """Header detection applies to the header ROW only — data rows keep every cell."""
    assert _parse('word,备注\nalpha,第一个\nbeta,第二个\n', 'words.csv').words == [
        'alpha', '第一个', 'beta', '第二个',
    ]


def test_csv_header_detection_is_not_applied_twice():
    """A data row that happens to repeat the header word is data, not a second header."""
    assert _parse('word\nalpha\nword\n', 'words.csv').words == ['alpha', 'word']


def test_txt_has_no_header_convention():
    """A txt whose first line literally reads `word` is data, not a header."""
    assert _parse('word\nalpha\n').words == ['word', 'alpha']


# ---------------------------------------------------------------------------
# Failure paths → 11301 (spec AC-05)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'content, filename',
    [
        ('', 'words.txt'),
        ('   \n\n  \n', 'words.txt'),
        (',,,,;;;||||', 'words.txt'),
        ('\n\n\n', 'words.csv'),
    ],
)
def test_empty_or_separator_only_file_raises_11301(content, filename):
    with pytest.raises(PlatformWordFileParseError) as excinfo:
        _parse(content, filename)
    assert excinfo.value.code == 11301


def test_unsupported_extension_raises_11301():
    with pytest.raises(PlatformWordFileParseError):
        _parse('whatever', 'words.pdf')


def test_corrupt_xlsx_raises_11301():
    """A file named .xlsx that is not a workbook must fail loudly, not import garbage."""
    with pytest.raises(PlatformWordFileParseError):
        PlatformWordService.parse_words_file(b'not really a workbook', 'words.xlsx')


def test_overlong_word_is_rejected_not_truncated():
    """Words longer than the column length are dropped, and reported as dropped.

    Truncating would store a word that never matches the text it was meant to
    catch, and would silently collide with a different over-long word sharing
    the same prefix — a wrong match is worse than a missing one.
    """
    overlong = 'x' * 300
    parsed = _parse(f'alpha\n{overlong}\nbeta\n')
    assert parsed.words == ['alpha', 'beta']
    assert parsed.dropped_overlong == 1


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------


def _xlsx_bytes(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_first_row_is_header_and_rest_are_words():
    raw = _xlsx_bytes([['敏感词'], ['alpha'], ['beta']])
    assert PlatformWordService.parse_words_file(raw, 'words.xlsx').words == ['alpha', 'beta']


def test_xlsx_multiple_columns_are_all_read():
    raw = _xlsx_bytes([['word', 'note'], ['alpha', 'A'], ['beta', 'B']])
    assert PlatformWordService.parse_words_file(raw, 'words.xlsx').words == [
        'alpha', 'A', 'beta', 'B',
    ]


def test_xlsx_with_only_a_header_raises_11301():
    raw = _xlsx_bytes([['敏感词']])
    with pytest.raises(PlatformWordFileParseError) as excinfo:
        PlatformWordService.parse_words_file(raw, 'words.xlsx')
    assert excinfo.value.code == 11301


def test_xlsx_skips_empty_cells_without_importing_placeholders():
    raw = _xlsx_bytes([['word', 'note'], ['alpha', None], [None, 'B']])
    assert PlatformWordService.parse_words_file(raw, 'words.xlsx').words == ['alpha', 'B']


# ---------------------------------------------------------------------------
# Diff preview (spec AC-03)
# ---------------------------------------------------------------------------


def test_overwrite_preview_reports_added_removed_and_incoming_count():
    preview = PlatformWordService.preview_import(
        _parse('alpha,beta,gamma'),
        ImportMode.OVERWRITE,
        existing=['alpha', 'delta'],
    )
    assert set(preview.added) == {'beta', 'gamma'}
    assert set(preview.removed) == {'delta'}
    assert preview.incoming == 3


def test_append_preview_never_removes_anything():
    preview = PlatformWordService.preview_import(
        _parse('alpha,beta'),
        ImportMode.APPEND,
        existing=['alpha', 'delta'],
    )
    assert set(preview.added) == {'beta'}
    assert preview.removed == []
    assert preview.incoming == 2


def test_preview_of_identical_content_is_a_no_op():
    """Importing the same file twice reports nothing to do (spec §3 边界)."""
    preview = PlatformWordService.preview_import(
        _parse('alpha,beta'),
        ImportMode.OVERWRITE,
        existing=['alpha', 'beta'],
    )
    assert preview.added == []
    assert preview.removed == []
    assert preview.incoming == 2


def test_preview_carries_the_parse_stats_through():
    """The dialog shows one set of numbers — they must describe the same parse
    that the confirmed import will execute."""
    preview = PlatformWordService.preview_import(
        _parse('a,a,b,' + 'x' * 300),
        ImportMode.OVERWRITE,
        existing=[],
    )
    assert preview.duplicates == 1
    assert preview.dropped_overlong == 1
    assert preview.incoming == 2


def test_preview_is_empty_against_an_empty_library():
    preview = PlatformWordService.preview_import(_parse('a,b'), ImportMode.OVERWRITE, existing=[])
    assert set(preview.added) == {'a', 'b'}
    assert preview.removed == []


def test_utf16_without_bom_is_rejected_not_imported_as_garbage():
    """A BOM-less UTF-16 file decodes "successfully" as UTF-8 — every other byte
    is NUL — so it would import NUL-laden words that can never match anything.

    The import would report SUCCESS and quietly pollute the library, instead of
    failing with 11301 (spec AC-05). Encoding is not detectable from content
    alone here, but a NUL in a word list is never intended, so it is treated as
    the decode failure it is.
    """
    with pytest.raises(PlatformWordFileParseError) as excinfo:
        PlatformWordService.parse_words_file('alpha\nbeta'.encode('utf-16-le'), 'words.txt')
    assert excinfo.value.code == 11301


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'gbk'])
def test_real_text_encodings_still_import(encoding):
    """The NUL guard must not reject the encodings users actually upload."""
    parsed = PlatformWordService.parse_words_file('alpha\nbeta\n'.encode(encoding), 'words.txt')
    assert parsed.words == ['alpha', 'beta']


def test_gbk_chinese_words_import():
    """GBK is the realistic alternative from a Chinese Windows desktop."""
    parsed = PlatformWordService.parse_words_file('中文词\n第二个词\n'.encode('gbk'), 'words.txt')
    assert parsed.words == ['中文词', '第二个词']
