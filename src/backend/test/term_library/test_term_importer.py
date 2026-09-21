"""F060 T-09: importing terms from a file.

The column semantics are design decision 8, and they are the whole reason this
file exists separately from F059's importer: a term needs a standard name, its
aliases AND a definition, so the file is three columns — while the sensitive-word
library reads every cell as one token. What the two share is the reading layer
(encoding, workbook, header detection), which `test_shared_tabular_import.py`
covers.

Three of these tests are here rather than in T-05's file because they need BOTH
importers to exist:

  * the same bad bytes must fail in both, since the contract's requirement is
    that one shared reader serves them;
  * the term importer must really go through that reader, or the sharing is
    nominal and the two will drift.

The DAO is stubbed (an I/O edge); the parsing, validation and mode handling —
the subject — are never mocked away.
"""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.term_library import (
    TermImportFileParseError,
    TermLibraryLimitExceededError,
)
from bisheng.term_library.domain.services import term_importer


def _csv(rows: list[list[str]]) -> bytes:
    """A csv as an admin actually uploads one — comma-separated.

    The shared reader splits on TABS, so a comma-separated line arrives as a
    single cell and the importer has to cut it into columns itself. Writing the
    fixture tab-separated would quietly skip that path: the tests would pass
    while every real csv imported as one column.
    """
    return '\n'.join(','.join(row) for row in rows).encode('utf-8')


def _xlsx(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _existing(*terms):
    """Existing rows as the DAO returns them."""
    return [
        SimpleNamespace(
            id=i + 1, standard_term=t[0], definition=t[1], aliases=list(t[2]), department_id=None
        )
        for i, t in enumerate(terms)
    ]


def _patch_dao(existing=None, count=0):
    """Stub the two DAOs the importer talks to.

    Keyed by NAME rather than returned as a tuple: an index-based table silently
    breaks the moment a row is added or reordered, and every test in the file
    shares it.
    """
    existing = existing or []
    created: list[dict] = []
    cleared: list[list] = []

    async def _acreate(data):
        created.append(data)
        return SimpleNamespace(id=len(created) + 100, **data)

    async def _adelete_these(terms):
        cleared.append(list(terms))
        return len(terms)

    return {
        'alist_all': patch.object(term_importer.TermLibraryTermDao, 'alist_all', AsyncMock(return_value=existing)),
        'acount': patch.object(term_importer.TermLibraryTermDao, 'acount', AsyncMock(return_value=count)),
        'acreate': patch.object(term_importer.TermLibraryTermDao, 'acreate', _acreate),
        'adelete': patch.object(term_importer.TermLibraryTermDao, 'adelete', AsyncMock()),
        'adelete_these': patch.object(term_importer.TermLibraryTermDao, 'adelete_these', _adelete_these),
        'created': created,
        'cleared': cleared,
    }


async def _preview(raw, filename, mode='append', existing=None, count=0):
    stubs = _patch_dao(existing, count)
    with stubs['alist_all'], stubs['acount'], stubs['acreate'], stubs['adelete'], stubs['adelete_these']:
        return await term_importer.preview_import(raw, filename, mode=mode)


async def _import(raw, filename, mode='append', existing=None, count=0):
    stubs = _patch_dao(existing, count)
    with stubs['alist_all'], stubs['acount'], stubs['acreate'], stubs['adelete'], stubs['adelete_these']:
        result = await term_importer.import_terms(raw, filename, mode=mode)
    return result, stubs['created']


# ── AC-07: the three-column format ───────────────────────────────────────────


async def test_a_three_column_csv_is_parsed():
    raw = _csv([['不良贷款率', '不良率;NPL', '不良贷款占贷款总额的比例']])

    preview = await _preview(raw, 'terms.csv')

    assert preview.added == 1


async def test_an_xlsx_is_parsed():
    raw = _xlsx([['不良贷款率', '不良率;NPL', '不良贷款占贷款总额的比例']])

    preview = await _preview(raw, 'terms.xlsx')

    assert preview.added == 1


async def test_a_header_row_is_skipped():
    """The header is optional but common; reading it as data would import a term
    literally named 标准词."""
    raw = _csv([['标准词', '别名', '释义'], ['不良贷款率', '不良率', '比例']])

    preview = await _preview(raw, 'terms.csv')

    assert preview.added == 1


async def test_the_alias_column_is_split_into_several_aliases():
    raw = _csv([['不良贷款率', '不良率;NPL', '比例']])

    _, created = await _import(raw, 'terms.csv')

    assert created[0]['aliases'] == ['不良率', 'NPL']


async def test_an_empty_alias_cell_is_allowed():
    """Most terms have no shorthand; requiring the column would mean inventing
    one."""
    raw = _csv([['不良贷款率', '', '比例']])

    _, created = await _import(raw, 'terms.csv')

    assert created[0]['aliases'] == []


async def test_txt_is_rejected_even_when_it_holds_three_columns():
    """Design decision 8: txt is refused at the door. A three-column txt would
    otherwise parse — and the feature would have shipped a format it does not
    document, tested by nothing."""
    raw = _csv([['不良贷款率', '不良率', '比例']])

    with pytest.raises(TermImportFileParseError):
        await _preview(raw, 'terms.txt')


async def test_the_rejection_of_txt_says_it_is_the_format():
    """The reason matters: this file's ROWS are fine, and an admin told "no
    definition" would go looking for a problem that is not there."""
    raw = _csv([['不良贷款率', '不良率', '比例']])

    with pytest.raises(TermImportFileParseError) as caught:
        await _preview(raw, 'terms.txt')

    assert 'txt' in str(caught.value), f'expected the format to be named, got: {caught.value}'


async def test_a_row_missing_the_definition_is_rejected():
    """AC-10: an import obeys the same rules as manual entry, and a definition
    is required there."""
    with pytest.raises(TermImportFileParseError):
        await _preview(_csv([['不良贷款率', '不良率']]), 'terms.csv')


async def test_a_row_with_a_blank_definition_is_rejected():
    with pytest.raises(TermImportFileParseError):
        await _preview(_csv([['不良贷款率', '不良率', '   ']]), 'terms.csv')


async def test_an_empty_file_is_rejected():
    with pytest.raises(TermImportFileParseError):
        await _preview(b'', 'terms.csv')


async def test_a_file_with_no_usable_rows_is_rejected():
    """Header only — parsed fine, but nothing to import."""
    with pytest.raises(TermImportFileParseError):
        await _preview(_csv([['标准词', '别名', '释义']]), 'terms.csv')


# ── AC-07: the preview reports what would change ─────────────────────────────


async def test_the_preview_reports_the_counts():
    raw = _csv([['A', '', 'a'], ['B', '', 'b'], ['A', '', 'a']])

    preview = await _preview(raw, 'terms.csv')

    assert preview.added == 2


async def test_the_preview_writes_nothing():
    raw = _csv([['A', '', 'a']])

    stubs = _patch_dao()
    with stubs['alist_all'], stubs['acount'], stubs['acreate'], stubs['adelete'], stubs['adelete_these']:
        await term_importer.preview_import(raw, 'terms.csv', mode='append')

    assert stubs['created'] == [], 'preview_import persisted something'


async def test_a_row_duplicating_an_existing_term_is_counted_not_added():
    raw = _csv([['A', '', 'a'], ['B', '', 'b']])

    preview = await _preview(raw, 'terms.csv', existing=_existing(('A', 'a', [])))

    assert preview.added == 1
    assert preview.duplicates == 1


async def test_two_rows_in_the_file_colliding_with_each_other_count_as_one_add():
    """The file is its own source of conflicts, not just the library: two rows
    naming the same term would otherwise be written twice."""
    raw = _csv([['A', '', 'a'], ['A', '', 'a']])

    preview = await _preview(raw, 'terms.csv')

    assert preview.added == 1
    assert preview.duplicates == 1


# ── AC-08: append and overwrite ──────────────────────────────────────────────


async def test_append_keeps_the_existing_terms():
    raw = _csv([['B', '', 'b']])

    _, created = await _import(raw, 'terms.csv', mode='append', existing=_existing(('A', 'a', [])))

    assert [c['standard_term'] for c in created] == ['B']


async def test_overwrite_removes_the_terms_not_in_the_file():
    raw = _csv([['B', '', 'b']])

    stubs = _patch_dao(existing=_existing(('A', 'a', [])))
    with stubs['alist_all'], stubs['acount'], stubs['acreate'], stubs['adelete'], stubs['adelete_these']:
        await term_importer.import_terms(raw, 'terms.csv', mode='overwrite')

    assert len(stubs['cleared']) == 1, (
        'overwrite mode did not clear the library, so it would append instead'
    )
    assert [t.standard_term for t in stubs['cleared'][0]] == ['A']
    assert [c['standard_term'] for c in stubs['created']] == ['B']


async def test_overwrite_does_not_clear_when_every_row_is_dropped():
    """The wipe this test exists for: a file that is NOT blank, so it clears the
    blankness check, but whose every row is dropped as over-long.

    Without the guard this deleted the whole library and wrote nothing — a
    permanent, unrecoverable wipe (the delete is a hard delete) triggered by an
    export that merely had long definitions in it. AC-09 says a file with no
    usable rows is rejected and writes nothing; "writes nothing" was true here
    while "rejected" was not, which is exactly the dangerous half.
    """
    # 释义 201 字 > MAX_DEFINITION_LENGTH, so every row is dropped.
    raw = _csv([['A', '', '长' * 201], ['B', '', '长' * 202]])

    stubs = _patch_dao(existing=_existing(('旧A', 'a', []), ('旧B', 'b', [])))
    with stubs['alist_all'], stubs['acount'], stubs['acreate'], stubs['adelete'], stubs['adelete_these']:
        with pytest.raises(TermImportFileParseError):
            await term_importer.import_terms(raw, 'terms.csv', mode='overwrite')

    assert stubs['cleared'] == [], 'the library was wiped by a file with no importable rows'
    assert stubs['created'] == []


async def test_append_of_the_same_file_is_still_allowed():
    """The counterpart: the empty plan is only dangerous in the DESTRUCTIVE
    mode. Append writes nothing and removes nothing, so it must not be turned
    into an error — an admin appending a file whose rows all collide has a
    no-op, not a failure."""
    raw = _csv([['A', '', '长' * 201]])

    preview = await _preview(raw, 'terms.csv', mode='append', existing=_existing(('旧A', 'a', [])))

    assert preview.added == 0
    assert preview.dropped_overlong == 1


async def test_overwrite_does_not_clear_when_the_file_is_unusable():
    """The order matters: parse and validate FIRST, then clear. Clearing first
    would empty a library for a file that turned out to be garbage — data loss
    caused by a typo in the filename."""
    stubs = _patch_dao(existing=_existing(('A', 'a', [])))
    with stubs['alist_all'], stubs['acount'], stubs['acreate'], stubs['adelete'], stubs['adelete_these']:
        with pytest.raises(TermImportFileParseError):
            await term_importer.import_terms(_csv([['X']]), 'terms.csv', mode='overwrite')

    assert stubs['cleared'] == [], 'the library was cleared for a file that could not be read'


async def test_the_preview_reports_what_overwrite_would_remove():
    """AC-08's destructive-mode warning needs the count, not just 'this will
    replace everything'."""
    raw = _csv([['B', '', 'b']])

    preview = await _preview(raw, 'terms.csv', mode='overwrite', existing=_existing(('A', 'a', []), ('C', 'c', [])))

    assert preview.removed == 2
    assert preview.added == 1


# ── AC-10: the import obeys the same rules as manual entry ───────────────────


async def test_the_import_respects_the_library_cap():
    from bisheng.term_library.domain.models.term import MAX_TERMS_PER_TENANT

    raw = _csv([['B', '', 'b']])

    with pytest.raises(TermLibraryLimitExceededError):
        await _import(raw, 'terms.csv', mode='append', count=MAX_TERMS_PER_TENANT)


async def test_the_import_rejects_a_duplicate_inside_the_file_against_an_alias():
    """Uniqueness spans aliases, so a new row whose STANDARD name is an existing
    term's alias is a conflict — same rule as manual entry."""
    raw = _csv([['不良率', '', '比例']])

    preview = await _preview(raw, 'terms.csv', existing=_existing(('不良贷款率', '比例', ['不良率'])))

    assert preview.added == 0
    assert preview.duplicates == 1


# ── the two assertions moved here from T-05 ──────────────────────────────────


def test_the_term_importer_reads_through_the_shared_layer():
    """Not "behaviour matches" but "the same code runs". Two implementations
    that agree today drift the first time one is fixed, and the contract's point
    was that there should not be two."""
    from bisheng.sensitive_word.domain.services import tabular_import

    original = tabular_import.read_rows
    tabular_import.read_rows = lambda raw, filename, **kw: [['A', '', 'a']]
    try:
        rows = term_importer.read_term_rows(b'x', 'terms.csv')
    finally:
        tabular_import.read_rows = original

    assert rows == [['A', '', 'a']]


@pytest.mark.parametrize(
    'raw, filename',
    [
        (b'', 'terms.csv'),
        (b'', 'terms.xlsx'),
        ('中文'.encode('gbk')[:2], 'terms.csv'),
        (b'\xff\xfe\x00\x00bad', 'terms.csv'),
        (b'not a zip at all', 'terms.xlsx'),
    ],
)
def test_a_file_that_fails_in_one_importer_fails_in_the_other(raw, filename):
    """The contract's requirement stated as the failure it forbids: the same
    bytes must not parse in one feature and not the other.

    Both raise their OWN error type (11301 vs 11505) — that difference is
    intended, the codes belong to different modules. What must be identical is
    the read/fail verdict.
    """
    from bisheng.common.errcode.platform_sensitive_word import PlatformWordFileParseError
    from bisheng.sensitive_word.domain.services.platform_word_service import PlatformWordService

    def _word_verdict():
        try:
            PlatformWordService.parse_words_file(raw, filename)
            return 'ok'
        except PlatformWordFileParseError:
            return 'failed'

    def _term_verdict():
        try:
            term_importer.read_term_rows(raw, filename)
            return 'ok'
        except TermImportFileParseError:
            return 'failed'

    assert _word_verdict() == _term_verdict(), (
        f'{filename!r} reads differently in the two importers -- they have drifted '
        'apart on the shared reading layer'
    )


# ── M1 (code review): a definition containing its own comma ──────────────────
#
# The first version split every comma-separated line on a comma class, which cut
# a definition at its own punctuation and kept only the first half — silently,
# with the preview reporting a successful import. A definition is prose; commas
# in it are the norm, not an edge case.

# ruff: noqa: RUF001 -- the fullwidth comma below IS the character under test.


async def test_a_definition_may_contain_a_comma():
    """The csv reader quotes such a cell, and it must survive whole."""
    raw = _csv([['不良贷款率', '不良率', '"指逾期90天以上的贷款,含次级与可疑类"']])

    _, created = await _import(raw, 'terms.csv')

    assert created[0]['definition'] == '指逾期90天以上的贷款,含次级与可疑类'


async def test_a_definition_may_contain_a_fullwidth_comma():
    """The Chinese comma is the one an admin actually types. It is not a column
    separator — the separator is ASCII — so an unquoted one must not split."""
    raw = _csv([['不良贷款率', '不良率', '指逾期90天以上的贷款，含次级与可疑类']])

    _, created = await _import(raw, 'terms.csv')

    assert created[0]['definition'] == '指逾期90天以上的贷款，含次级与可疑类'


async def test_a_definition_may_contain_a_quoted_comma_and_an_alias_may_be_empty():
    """Both at once: the empty alias column must still be an empty column."""
    raw = _csv([['不良贷款率', '', '"比例,含次级"']])

    _, created = await _import(raw, 'terms.csv')

    assert created[0]['aliases'] == []
    assert created[0]['definition'] == '比例,含次级'
