"""Reading an uploaded tabular file into rows — the shared half of two importers.

Both the platform sensitive-word library (F059) and the term library (F060) accept
an admin-uploaded file. What they share is the container handling: which
extensions are allowed, how to decode a blob that may be UTF-8 with a BOM or GBK,
how to read a workbook, and how to tell a header row from data. What they do NOT
share is what the cells MEAN — the word library treats every cell as one token,
the term library reads the first three columns as standard term, aliases and
definition.

The contract's requirement is "F060 reuses F059's parser rather than writing a
second one", and this module is what makes that true in the only sense it can be:
one implementation of the encoding and container handling, so a file that reads in
one feature cannot fail to read in the other. Column semantics stay with each
caller (design decision 8).

The extraction is deliberately mechanical — this file contains no logic that did
not already exist in `PlatformWordService`, and that class's public behaviour is
unchanged. It exists so the two importers cannot drift on the parts that are
genuinely identical; a divergence there would surface as "this file works for
sensitive words but not for terms", which is exactly the confusing asymmetry the
contract asked us to avoid.

Errors are returned as a plain exception type rather than either feature's error
code: the caller wraps it in its own (11301 for the word library, 11505 for
terms). That keeps this module free of any feature's vocabulary — and keeps each
feature reporting failures in its own error module.
"""

from __future__ import annotations

import csv
import io

#: Extensions both importers accept. Deliberately excludes `txt`: the term
#: library needs three columns and a plain text word list cannot carry them
#: (design decision 8), while the word library has no use for a tabular reader.
SUPPORTED_EXTENSIONS = ('txt', 'csv', 'xlsx')

#: Only tabular formats carry a header convention; a txt first line is data.
HEADER_EXTENSIONS = ('csv', 'xlsx')

#: First-row cells that identify a header row. Matched case-insensitively.
#:
#: A first row counts as a header when ANY of its cells is one of these — real
#: exports label the column and may carry other columns beside it, so requiring
#: every cell to be a known token would miss the common case.
HEADER_TOKENS = frozenset(
    {
        'word', 'words', 'keyword', 'keywords', 'term', 'terms', 'definition', 'alias', 'aliases',
        '敏感词', '敏感词库', '关键词', '词条', '词表', '释义', '别名',
    }
)


class TabularImportError(Exception):
    """The file could not be read at all (bad extension, undecodable, unreadable).

    Carries no error code on purpose — see the module docstring. Callers wrap it.
    """


def file_extension(filename: str) -> str:
    """The lowercase extension, or '' when the name has none."""
    return (filename or '').rsplit('.', 1)[-1].lower()


def _pad(rows: list[list[str]]) -> list[list[str]]:
    """Widen every row to the longest, so callers can index columns safely.

    `csv.reader` yields variable-length rows: a spreadsheet omits trailing empty
    cells rather than writing a comma for each, so `标准词,,释义` can arrive as
    `['标准词', '', '释义']` while `标准词,别名,` arrives as `['标准词', '别名']`.
    A caller indexing `row[2]` would then be reading a column that exists for
    some rows and not others. Padding keeps "absent column" and "empty column"
    the same thing, which is what the callers already assume.
    """
    width = max((len(row) for row in rows), default=0)
    return [row + [''] * (width - len(row)) for row in rows]


def read_rows(raw: bytes, filename: str, *, allowed: tuple[str, ...] = SUPPORTED_EXTENSIONS) -> list[list[str]]:
    """Decode an upload into rows of raw cells, without interpreting them.

    `allowed` lets a caller narrow the accepted formats — the term library passes
    a set without `txt` (design decision 8) — without duplicating the extension
    check. Raising the same error for "not in the allowed set" and "not a known
    format" is deliberate: both mean the same thing to the admin.
    """
    extension = file_extension(filename)
    if extension not in allowed:
        raise TabularImportError(f'Unsupported file type: {extension or "(none)"}')

    if extension == 'xlsx':
        return read_xlsx(raw)
    if extension == 'csv':
        return read_csv(raw)
    return read_text(raw)


def _decode(raw: bytes) -> str:
    """The bytes as text, with the two encodings a Chinese desktop actually uses.

    `utf-8-sig` also strips a BOM, which Excel writes into "csv" exports and which
    would otherwise become part of the first word.
    """
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        # GBK is the realistic alternative for files exported on a Chinese Windows
        # desktop; failing outright on them would be unhelpful.
        try:
            text = raw.decode('gbk')
        except UnicodeDecodeError as exc:
            raise TabularImportError('could not decode as utf-8 or gbk') from exc

    # A UTF-16 file WITHOUT a BOM decodes "successfully" as UTF-8 (every other byte
    # is NUL), so it would import as NUL-laden values that can never match
    # anything — reporting SUCCESS and polluting the library instead of failing. A
    # NUL in a word list is never intended, so treat it as the decode failure it is.
    if '\x00' in text:
        raise TabularImportError('file contains NUL bytes (UTF-16 without BOM?)')
    return text


def read_text(raw: bytes) -> list[list[str]]:
    """Decode a txt blob into rows of raw cells (not yet split).

    Tab-separated, because txt carries no column convention of its own and a tab
    is the one separator a word list will not contain by accident.
    """
    return [line.split('\t') for line in _decode(raw).splitlines()]


def read_csv(raw: bytes) -> list[list[str]]:
    """Decode a csv blob with the standard quoting rules.

    **Quoting is the whole point of this function existing separately from
    `read_text`.** A cell whose own text contains a comma is quoted by every
    spreadsheet that writes csv (`"a definition, with a comma"`); splitting on
    commas naively cuts such a cell in half and silently keeps only the first
    part. `csv.reader` handles quoting and escaped quotes, and its delimiter is
    set to a comma so a fullwidth comma stays ordinary prose punctuation —
    text already quoted, and text that legitimately contains only fullwidth
    commas, both survive intact.

    Rows are padded to a common width by `_pad`, so a row that simply omits
    trailing empty cells still lines up with the others.
    """
    rows = list(csv.reader(io.StringIO(_decode(raw))))
    return _pad([row for row in rows if row != []])


def read_xlsx(raw: bytes) -> list[list[str]]:
    """Read the active sheet into rows of strings.

    Every workbook error (bad zip, corrupt sheet, missing dependency) means the
    same thing to the caller, so they all collapse into one exception.
    """
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(filename=io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise TabularImportError('workbook could not be opened') from exc

    rows: list[list[str]] = []
    try:
        for row in workbook.active.iter_rows(values_only=True):
            rows.append(['' if cell is None else str(cell) for cell in row])
    finally:
        workbook.close()
    return rows


def looks_like_header(row: list[str], *, split_cell) -> bool:
    """True when the first row is a header rather than data.

    `split_cell` is injected because splitting a cell is the one column-semantics
    step this function needs: a csv line like `敏感词,备注` arrives as a SINGLE cell
    (only tabs separate cell boundaries at read time), so a whole-cell comparison
    would never see the token inside it. Each feature passes its own splitter, so
    the header question is answered with the same rules that will later read the
    values.
    """
    return any(
        token.lower() in HEADER_TOKENS
        for cell in row
        for token in split_cell(cell)
    )


def strip_header(rows: list[list[str]], *, split_cell) -> list[list[str]]:
    """Drop the first row when it looks like a header. Returns rows unchanged otherwise."""
    if rows and looks_like_header(rows[0], split_cell=split_cell):
        return rows[1:]
    return rows
