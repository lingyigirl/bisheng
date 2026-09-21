"""Importing terms from an uploaded file (F060, spec AC-07 ~ AC-11).

A term is three things — a standard name, its aliases and a definition — so the
file is three columns in that order: `标准词, 别名, 释义`. That format is design
decision 8, and it is the reason this module stands beside F059's importer rather
than inside it: the two share the READING layer (extension check, encoding
fallback, workbook, header detection — `tabular_import`) and share nothing about
what a column means. The sensitive-word library reads every cell as one token.

`txt` is refused at the door. A three-column file saved as txt is a csv with a
different extension, and a one-token-per-line file cannot carry a definition —
so the rule that requires one (AC-10) would reject every row, and the admin would
watch a successful-looking upload import nothing.

**Both modes validate before either writes.** For APPEND that is merely tidy; for
OVERWRITE it is the difference between "the file was not usable" and "the
library is now empty because the wrong file was chosen".

Recorded limitation: rows are written one INSERT at a time (the DAO's shape), so
an OVERWRITE that fails midway leaves a partial library rather than rolling back.
The file is still in the admin's hands, so it is recoverable by re-importing —
but it is NOT atomic the way F059's import is.
"""

# ruff: noqa: RUF001 -- the fullwidth comma/semicolon are the separators an
# admin's spreadsheet actually contains; folding them to ASCII would silently
# stop splitting those columns.

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from bisheng.common.errcode.term_library import (
    TermImportFileParseError,
    TermLibraryLimitExceededError,
)
from bisheng.sensitive_word.domain.services import tabular_import
from bisheng.sensitive_word.domain.services.tabular_import import TabularImportError
from bisheng.term_library.domain.models.dao import TermLibraryTermDao
from bisheng.term_library.domain.models.term import (
    MAX_DEFINITION_LENGTH,
    MAX_STANDARD_TERM_LENGTH,
    MAX_TERMS_PER_TENANT,
)

#: What an import accepts. `txt` is deliberately absent (design decision 8).
TERM_EXTENSIONS = ('csv', 'xlsx')

#: Column separator for a row that arrived as ONE cell — the tab-separated
#: `.txt`-style line, and nothing else. csv and xlsx rows are already split by
#: `tabular_import` (csv through the stdlib reader, so quoting is honoured), and
#: running this over an already-split row is what used to truncate a definition
#: at its first comma.
#:
#: Deliberately NOT a repeated separator: two commas in a row must produce an
#: EMPTY column, not one. `标准词,,释义` is a term with no alias — the most common
#: row there is — and collapsing the pair would delete the alias column and
#: shift the definition into its place, rejecting every such row.
COLUMN_SEPARATOR_RE = re.compile(r'[,，]')

#: Separator inside the alias column: `不良率;NPL` is two aliases. Punctuation
#: only — a space can be part of a term.
ALIAS_SEPARATOR_RE = re.compile(r'[;；|、,，]+')


class ImportMode(str, Enum):  # noqa: UP042 - matches the sibling importer's str-Enum idiom
    OVERWRITE = 'overwrite'
    APPEND = 'append'


@dataclass
class TermImportPreview:
    """What an import WOULD change — and, after `import_terms`, what it did.

    Counts rather than name lists (unlike F059's preview, which lists words):
    the confirmation dialog shows these numbers, and a term is a name plus a
    definition and a set of aliases — a list of them would not fit it.
    """

    #: Rows that will be (or were) written.
    added: int = 0
    #: Rows naming a term the library already has — skipped, never merged.
    duplicates: int = 0
    #: Rows too long for their column — skipped, because the rest of the file is
    #: still usable and an aborted import would help nobody (spec AC-07).
    dropped_overlong: int = 0
    #: Existing terms that OVERWRITE will leave gone. Always 0 for APPEND.
    removed: int = 0


def _split_for_header(cell: str) -> list[str]:
    """Tokens of one cell, for header detection only.

    The shared reader asks each feature for its own splitter because a csv line
    reaches it whole: `标准词,别名,释义` is one cell, and comparing the whole cell
    against the header tokens would never match.
    """
    return [token.strip() for token in COLUMN_SEPARATOR_RE.split(cell or '') if token.strip()]


def read_term_rows(raw: bytes, filename: str) -> list[list[str]]:
    """Read an upload into rows of cells, with the term library's two additions.

    Those additions are the extension set (no txt) and the header convention (a
    labelled first row is dropped). Interpreting the cells is the caller's job —
    this is the function both importers are required to agree on (spec AC-11),
    so it must stay free of column semantics.
    """
    try:
        rows = tabular_import.read_rows(raw, filename, allowed=TERM_EXTENSIONS)
    except TabularImportError as exc:
        raise TermImportFileParseError(exception=exc) from exc

    # Unconditional, not gated on the extension: every format this library
    # accepts is tabular, and a header is a convention of tabular files.
    rows = tabular_import.strip_header(rows, split_cell=_split_for_header)
    if not any(cell.strip() for row in rows for cell in row):
        # A header with no data under it, or blank lines only. Reported as a
        # failure rather than as "imported 0 terms", which reads like a bug.
        raise TermImportFileParseError(msg='文件中没有可导入的词条')
    return rows


def _split_columns(row: list[str]) -> list[str]:
    """One row as columns.

    A csv or xlsx row already arrives as cells — `tabular_import` parses csv with
    the stdlib reader, so quoting is honoured and a definition may contain a comma
    of its own. This only has to split a row that arrived as a SINGLE cell, which
    is the tab-separated case (a `.txt`-style line) and nothing else: splitting an
    already-split row on commas is what silently truncated a definition at its
    first comma.

    Columns past the third are ignored — the format is three columns, and a
    spreadsheet often carries a fourth for the admin's own notes.
    """
    if len(row) == 1:
        return [cell.strip() for cell in COLUMN_SEPARATOR_RE.split(row[0])]
    return [cell.strip() for cell in row]


def _split_aliases(cell: str) -> list[str]:
    """The alias column as a list, without repeats and without blanks.

    Same shape as the service's own alias cleaning (AC-03): the column is
    optional, and most terms have no shorthand at all.
    """
    aliases: list[str] = []
    for token in ALIAS_SEPARATOR_RE.split(cell or ''):
        text = token.strip()
        if text and text not in aliases:
            aliases.append(text)
    return aliases


def _read_row(columns: list[str]) -> tuple[str, list[str], str]:
    """The standard term, aliases and definition of one data row.

    A missing or blank standard term or definition fails the WHOLE import rather
    than the row: the file's format is wrong, not one of its values, and the
    admin fixes that by exporting again — whereas a row that is merely too long
    is dropped and counted.

    The error carries 11505 rather than the 11502 a manual entry would raise on
    the same value: nobody is looking at a form, the message has to say "this
    file cannot be used" (spec AC-09).
    """
    standard = columns[0]
    if not standard:
        raise TermImportFileParseError(msg='标准词不能为空')
    if len(columns) < 3 or not columns[2]:
        raise TermImportFileParseError(msg='释义不能为空')
    return standard, _split_aliases(columns[1]), columns[2]


async def _plan(
    raw: bytes, filename: str, *, mode: str, user_id: int | None = None
) -> tuple[list[dict], TermImportPreview, list]:
    """Everything an import does except writing it: read, validate, count.

    Shared by preview and import so the numbers the admin confirms are produced
    by the code that will act on them, instead of by a second implementation
    that agrees with it today. Returns the rows to write, the preview, and the
    existing terms (which OVERWRITE needs to delete).
    """
    rows = read_term_rows(raw, filename)
    existing = await TermLibraryTermDao.alist_all()
    overwriting = mode == ImportMode.OVERWRITE

    # Names already spoken for. EMPTY for OVERWRITE: the library is being
    # replaced, so a row that matches an existing term is not a conflict — the
    # existing term is about to stop existing. Only the file's own repeats can
    # collide, which the same set catches as rows are planned.
    taken: set[str] = set()
    if not overwriting:
        for term in existing:
            taken.add(term.standard_term)
            taken.update(term.aliases or [])

    planned: list[dict] = []
    duplicates = 0
    dropped_overlong = 0
    for row in rows:
        columns = _split_columns(row)
        if not any(columns):
            continue  # a blank line inside the file is not a row
        standard, aliases, definition = _read_row(columns)

        if len(standard) > MAX_STANDARD_TERM_LENGTH or len(definition) > MAX_DEFINITION_LENGTH:
            dropped_overlong += 1
            continue

        names = {standard, *aliases}
        if names & taken:
            # Skipped, not merged: two rows claiming one name is exactly the
            # ambiguity AC-02/AC-03 forbid, and a silent merge would pick a
            # winner the admin never chose.
            duplicates += 1
            continue
        taken |= names
        planned.append(
            {
                'standard_term': standard,
                'definition': definition,
                'aliases': aliases,
                'create_user_id': user_id,
            }
        )

    count = await TermLibraryTermDao.acount()
    size_after = len(planned) if overwriting else count + len(planned)
    if size_after > MAX_TERMS_PER_TENANT:
        # Counted, not truncated: which terms to keep is the admin's call, and
        # the cap is a property of the library they are importing into (AC-06).
        raise TermLibraryLimitExceededError(msg=f'术语库最多 {MAX_TERMS_PER_TENANT} 条')

    removed = 0
    if overwriting:
        re_added = {item['standard_term'] for item in planned}
        removed = sum(1 for term in existing if term.standard_term not in re_added)

    return planned, TermImportPreview(added=len(planned), duplicates=duplicates,
                                      dropped_overlong=dropped_overlong, removed=removed), existing


async def preview_import(raw: bytes, filename: str, *, mode: str = ImportMode.APPEND) -> TermImportPreview:
    """What this file would change. Writes nothing (spec AC-09)."""
    _planned, preview, _existing = await _plan(raw, filename, mode=mode)
    return preview


async def import_terms(
    raw: bytes, filename: str, *, mode: str = ImportMode.APPEND, user_id: int | None = None
) -> TermImportPreview:
    """Apply an import, in one of two modes (spec AC-08).

    OVERWRITE clears the library first — but only after the file has been read
    and validated, which `_plan` has already done by the time anything is
    deleted. APPEND leaves existing terms alone and skips the incoming rows that
    would duplicate them.
    """
    planned, preview, existing = await _plan(raw, filename, mode=mode, user_id=user_id)

    if mode == ImportMode.OVERWRITE:
        # Nothing to write and everything to delete is the one outcome that must
        # never happen: it is a wipe caused by a file nobody could use, and the
        # delete is permanent. `_plan` only rejects a file that is entirely
        # blank, so a file whose every row was dropped as over-long still
        # reaches here with an empty plan (spec AC-09).
        if not planned:
            raise TermImportFileParseError(msg='文件没有可导入的词条，已取消覆盖')
        await TermLibraryTermDao.adelete_these(existing)
    for item in planned:
        await TermLibraryTermDao.acreate(item)

    logger.info(
        'term import finished: mode={} added={} duplicates={} dropped_overlong={} removed={}',
        mode,
        preview.added,
        preview.duplicates,
        preview.dropped_overlong,
        preview.removed,
    )
    return preview
