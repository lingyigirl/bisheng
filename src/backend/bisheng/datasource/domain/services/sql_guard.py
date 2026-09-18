"""SQL guard: decide whether a model-generated statement may reach the database.

Two tiers, and the tier that decided is reported back (spec AC-20 requires the
degradation to be observable):

  * **AST tier** — ``sqlglot`` parses the statement and the guard inspects the
    tree. Used whenever the parser can read the input.
  * **narrow tier** — keyword validation, used when the parser FAILS. A parse
    failure must NOT become a rejection: the platform can't read the dialect,
    which says nothing about whether the query is safe or legitimate. Rejecting
    on parse failure would make the feature unusable for exactly the queries
    that are hardest to express.

Three bypasses were MEASURED against sqlglot 30.18.0 before this module was
written. Each defeats a natural-looking implementation, so each has a named
check below and a test in ``test_sql_guard.py``:

  1. ``SELECT 1; DROP TABLE t`` — ``parse_one`` does NOT raise; it returns
     ``Block([Select, Drop])``. A guard asking "is the root a Select?" sees a
     Select and executes the DROP. -> :func:`_check_single_statement`

  2. ``WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`` — parses with a
     *Select* root; the Delete is a child node. -> :func:`_check_no_write_nodes`
     walks the WHOLE tree.

  3. ``SELECT * FROM t FOR UPDATE`` — parses, and the lock is STRIPPED on
     write-back: ``.sql()`` renders ``SELECT * FROM t``. It survives only as
     ``args['locks']``. -> :func:`_check_no_locking`

A fourth case runs the other way: some LEGITIMATE queries genuinely fail to
parse (Oracle/DM ``MODEL`` clauses, for instance). Those must not be rejected
just because the parser could not read them (AC-20), so they land in the narrow
tier -- which therefore has to recognise the locking keywords itself, or a
locking read reaches the database through the degraded path. See
``_NARROW_FORBIDDEN_RE``.

> **Correction to an earlier measurement.** This module previously recorded
> ``FOR UPDATE SKIP LOCKED`` as "a real statement sqlglot cannot read". That was
> wrong: the probe had been written ``SELECT ... FOR UPDATE SKIP LOCKED``, and
> ``...`` is not a valid column list, so the PLACEHOLDER caused the ParseError.
> With a real column list it parses and the AST tier rejects it via
> ``args['locks']``, exactly like plain ``FOR UPDATE``. The narrow tier's
> locking keywords are still required -- just for the ``MODEL``-clause class of
> queries, not for ``SKIP LOCKED``.

**This is not the security boundary.** Decision 8 in the datasource design is
explicit: the real boundary is the read-only database account, then the
read-only session, and this guard is the third layer. The three bypasses above
are why -- a guard that has already been circumvented three ways in a single
afternoon of testing is a useful filter, not a wall. Treating it as the sole
protection would be a mistake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# Which tier produced the verdict. Recorded on the query log so a degradation
# is visible rather than silent (spec AC-20).
DEGRADE_NONE = 'none'
DEGRADE_PARSE_FAILED = 'parse_failed'

# Engines mapped to the sqlglot dialect used to parse them.
#
# DaMeng has NO sqlglot dialect (34 built-ins, none is `dm`). It is parsed as
# `oracle`, and that is a considered choice rather than a fallback: 27 DM/Oracle
# constructs were measured (ROWNUM, (+) joins, CONNECT BY, NVL/DECODE, DUAL,
# FETCH FIRST, DM hints, VARCHAR2, @dblink, PARTITION, TABLE(), Chinese
# identifiers) and every one parsed under `oracle`. Routing DM to the narrow
# tier instead would silently weaken validation for what is, in this project,
# the most likely customer database.
_ENGINE_DIALECTS: dict[str, str] = {
    'mysql': 'mysql',
    'postgresql': 'postgres',
    'gaussdb': 'postgres',
    'oracle': 'oracle',
    'db2': 'db2',
    'dm': 'oracle',
}

# Node types that represent a write, a schema change or an authorization change.
# Matched by class name so a sqlglot upgrade that adds a node type does not
# silently drop it from the check -- see `_check_no_write_nodes`.
_FORBIDDEN_NODE_NAMES = frozenset(
    {
        'Insert',
        'Update',
        'Delete',
        'Drop',
        'Create',
        'Alter',
        'AlterTable',
        'CreateTable',
        'DropTable',
        'TruncateTable',
        'Merge',
        'Grant',
        'Revoke',
        'Copy',
        'Attach',
        'Detach',
        'Command',  # sqlglot's catch-all for statements it parses but has no node for
        'Commit',
        'Rollback',
        'Transaction',
        'Set',
        'Use',
        'Call',
        'Analyze',
        'Vacuum',
        'Comment',
    }
)

# Only these may be the statement that actually runs.
_ALLOWED_ROOT_NAMES = frozenset({'Select', 'Union', 'Intersect', 'Except', 'Subquery'})

# Narrow-tier keyword screen. Applied when the parser could not read the input,
# so it is the last line before the database.
#
# The locking keywords are load-bearing, and they are listed even though the AST
# tier also catches them: the narrow tier must not depend on the AST tier having
# run. The queries that reach here are legal-but-unparseable ones (Oracle/DM
# `MODEL` clauses and similar), and a `FOR UPDATE` appended to such a query is
# caught nowhere else.
_NARROW_FORBIDDEN_RE = re.compile(
    r'\b('
    r'insert|update|delete|drop|truncate|alter|create|rename|replace|merge|'
    r'grant|revoke|commit|rollback|savepoint|'
    r'for\s+update|for\s+share|lock\s+in\s+share\s+mode|'
    r'into\s+outfile|into\s+dumpfile|load_file|'
    r'set\s+transaction|set\s+session'
    r')\b',
    re.IGNORECASE,
)

# Keywords inside a string literal are data, not syntax. Stripped before the
# narrow screen so a legitimate query mentioning a keyword is not refused.
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

_NARROW_LEADING_RE = re.compile(r'^\s*(select|with)\b', re.IGNORECASE)


@dataclass(frozen=True)
class GuardVerdict:
    """Outcome of a validation.

    ``reason`` is None when allowed, and deliberately never quotes the
    statement: it is surfaced to callers and written to logs, and the statement
    can carry schema names and literal values.
    """

    allowed: bool
    reason: str | None = None
    degraded: str = DEGRADE_NONE


class SqlGuard:
    """Validates a single model-generated statement. Stateless."""

    @classmethod
    def validate(cls, statement: str, *, engine: str | None = None) -> GuardVerdict:
        """Decide whether ``statement`` may be executed.

        ``engine`` selects the dialect; unknown or absent engines fall back to
        sqlglot's generic dialect, which is more permissive than any specific
        one -- acceptable because a parse in the generic dialect still has to
        clear every structural check below.
        """
        text = (statement or '').strip()
        if not text:
            return GuardVerdict(False, 'empty statement')

        dialect = _ENGINE_DIALECTS.get((engine or '').lower())

        try:
            statements = sqlglot.parse(text, read=dialect)
        except Exception:
            # AC-20: unreadable is not the same as unsafe. Hand off to the
            # narrow tier and record the degradation.
            verdict = cls._validate_narrow(text)
            return GuardVerdict(verdict.allowed, verdict.reason, DEGRADE_PARSE_FAILED)

        # A trailing semicolon produces a trailing None/empty parse in some
        # dialects; drop those before counting, or every generated query ending
        # in `;` would look like multiple statements.
        parsed = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
        if not parsed:
            return GuardVerdict(False, 'empty statement')

        for check in (cls._check_single_statement, cls._check_root_is_a_query, cls._check_no_write_nodes, cls._check_no_locking):
            verdict = check(parsed)
            if not verdict.allowed:
                return GuardVerdict(False, verdict.reason, DEGRADE_NONE)

        return GuardVerdict(True)

    # --- AST tier ------------------------------------------------------------

    @staticmethod
    def _check_single_statement(parsed: list) -> GuardVerdict:
        """Bypass 1.

        `parse_one` does not raise on ``SELECT 1; DROP TABLE t`` -- it returns a
        Block holding BOTH statements. Counting is therefore the check; asking
        about the root node answers a different question.
        """
        if len(parsed) != 1:
            return GuardVerdict(False, 'only a single statement may be executed')
        return GuardVerdict(True)

    @staticmethod
    def _check_root_is_a_query(parsed: list) -> GuardVerdict:
        root = parsed[0]
        name = type(root).__name__
        if name not in _ALLOWED_ROOT_NAMES:
            return GuardVerdict(False, 'only query statements may be executed')
        return GuardVerdict(True)

    @staticmethod
    def _check_no_write_nodes(parsed: list) -> GuardVerdict:
        """Bypass 2.

        Walks the entire tree. The CTE case puts a ``Delete`` under a ``Select``
        root, so inspecting only the root -- or only the statement list -- misses
        it entirely.

        Matching is by class NAME rather than by ``isinstance`` against a list
        of classes: it keeps working when sqlglot adds or renames node types,
        and the failure mode of a name-based check (a node we don't know about
        slips through) is caught by ``_check_root_is_a_query`` for the root and
        by the narrow tier's keyword screen for the rest.
        """
        for statement in parsed:
            for node in statement.walk():
                if type(node).__name__ in _FORBIDDEN_NODE_NAMES:
                    return GuardVerdict(False, 'the statement contains a write or schema operation')
        return GuardVerdict(True)

    @staticmethod
    def _check_no_locking(parsed: list) -> GuardVerdict:
        """Bypass 3.

        The lock clause disappears when the AST is rendered back to SQL
        (``parse_one("SELECT * FROM t FOR UPDATE").sql()`` is ``"SELECT * FROM t"``),
        so it must be read from the node args, not from the text.

        Both ``locks`` (sqlglot's typed list) and a raw search of the expression
        args are checked: the typed slot is the documented one, and the fallback
        covers dialects that park the clause somewhere else.
        """
        for statement in parsed:
            for node in statement.walk():
                locks = node.args.get('locks')
                if locks:
                    return GuardVerdict(False, 'locking reads are not permitted')
        return GuardVerdict(True)

    # --- narrow tier ---------------------------------------------------------

    @classmethod
    def _validate_narrow(cls, text: str) -> GuardVerdict:
        """Keyword validation, used only when the parser could not read the input.

        Deliberately conservative in one direction only: it refuses anything it
        cannot positively recognise as a read, but it must not refuse a
        legitimate query merely because a keyword appears inside a string
        literal.
        """
        if not _NARROW_LEADING_RE.match(text):
            return GuardVerdict(False, 'only query statements may be executed')

        scrubbed = _STRING_LITERAL_RE.sub("''", text)

        # The reason this tier cannot be dropped: a legal-but-unparseable query
        # (DM/Oracle `MODEL` clause) with a locking read appended reaches the
        # database only through here.
        if _NARROW_FORBIDDEN_RE.search(scrubbed):
            return GuardVerdict(False, 'the statement contains a forbidden operation')

        # Multiple statements, judged on what remains after literals are
        # removed so a semicolon inside a string is not counted.
        if ';' in scrubbed.rstrip().rstrip(';'):
            return GuardVerdict(False, 'only a single statement may be executed')

        return GuardVerdict(True)
