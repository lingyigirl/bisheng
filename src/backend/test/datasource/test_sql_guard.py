"""SQL guard: only a single read-only SELECT may reach the database.

This is the feature's security boundary (spec AC-16/AC-17/AC-20), so the tests
below are written against *measured* parser behaviour rather than the intuitive
contract. Three bypasses were measured before the guard existed, and each one
defeats a natural-looking implementation:

  1. ``parse_one("SELECT 1; DROP TABLE t")`` does NOT raise. It returns a
     Block([Select, Drop]). A guard that asks "is the root node a Select?" sees
     a Select and lets the DROP through. Statement COUNT is the check.

  2. ``WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`` parses with a
     *Select* root; the Delete sits in a child node. Only a full-tree walk
     finds it.

  3. ``SELECT * FROM t FOR UPDATE`` parses, and the lock clause is STRIPPED on
     write-back -- ``.sql()`` renders ``SELECT * FROM t``. The lock survives
     only as ``args['locks']``. Searching the SQL text for "FOR UPDATE" is not
     equivalent either, because the guard sees the model's SQL, not the
     rendered form of the AST.

A fourth case cuts the other way: some legitimate queries genuinely fail to
parse, and spec AC-20 forbids rejecting a statement merely because the parser
could not read it. Those must fall to the narrow tier -- which then has to
recognise the locking keywords itself, or a locking read reaches the database
through the degraded path.

The degradation examples below are real DM/Oracle constructs (``MODEL`` clause,
``PIVOT``-with-``XMLAGG``), not syntax errors. An earlier round of this work
recorded ``FOR UPDATE SKIP LOCKED`` as the canonical unparseable statement --
that was wrong, and the error is worth preserving: the probe had been written
as ``SELECT ... FOR UPDATE SKIP LOCKED``, and ``...`` is not valid SQL, so the
PLACEHOLDER caused the ParseError, not the locking clause. With a real column
list it parses fine and the AST tier catches it via ``args['locks']``.
"""

import pytest

from bisheng.datasource.domain.services.sql_guard import (
    DEGRADE_NONE,
    DEGRADE_PARSE_FAILED,
    GuardVerdict,
    SqlGuard,
)

# --- the three measured bypasses ---------------------------------------------


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT 1; DROP TABLE t',
        'SELECT * FROM t; DELETE FROM t',
        'SELECT 1; UPDATE t SET a = 1',
        'DROP TABLE t',
        # A leading harmless statement must not launder the second one.
        'SELECT 1; SELECT 2',
    ],
)
def test_multiple_statements_are_rejected(statement):
    """Bypass 1. `parse_one` accepts all of these without raising, so the
    statement count -- not the root node -- has to decide."""
    verdict = SqlGuard.validate(statement)
    assert verdict.allowed is False, f'{statement!r} was allowed'
    assert verdict.reason


@pytest.mark.parametrize(
    'statement',
    [
        'WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x',
        'WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x',
        'SELECT * FROM (DELETE FROM t RETURNING *) x',
    ],
)
def test_write_hidden_in_a_cte_is_rejected(statement):
    """Bypass 2. The root node is a Select here; only walking the whole tree
    finds the write."""
    verdict = SqlGuard.validate(statement)
    assert verdict.allowed is False, f'{statement!r} was allowed'


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT * FROM t FOR UPDATE',
        'SELECT * FROM t FOR SHARE',
        'SELECT * FROM t LOCK IN SHARE MODE',
        'SELECT id FROM t WHERE id = 1 FOR UPDATE',
    ],
)
def test_locking_reads_are_rejected(statement):
    """Bypass 3, and the one with the quietest failure mode: the AST keeps the
    lock in `args['locks']` while `.sql()` renders without it, so any check
    based on the rendered text -- or on node types -- sees a plain SELECT."""
    verdict = SqlGuard.validate(statement)
    assert verdict.allowed is False, f'{statement!r} was allowed'


# --- plain writes ------------------------------------------------------------


@pytest.mark.parametrize(
    'statement',
    [
        'INSERT INTO t (a) VALUES (1)',
        'UPDATE t SET a = 1',
        'DELETE FROM t',
        'DROP TABLE t',
        'TRUNCATE TABLE t',
        'ALTER TABLE t ADD COLUMN c INT',
        'CREATE TABLE t2 (a INT)',
        'GRANT SELECT ON t TO u',
        'REVOKE SELECT ON t FROM u',
        'COMMIT',
        'SET TRANSACTION READ WRITE',
    ],
)
def test_non_query_statements_are_rejected(statement):
    assert SqlGuard.validate(statement).allowed is False, f'{statement!r} was allowed'


# --- legitimate statements pass ---------------------------------------------


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT 1',
        'SELECT id, name FROM customers WHERE id = 1',
        'SELECT * FROM a JOIN b ON a.id = b.a_id',
        'WITH x AS (SELECT id FROM t) SELECT * FROM x',
        'SELECT COUNT(*) FROM orders GROUP BY status HAVING COUNT(*) > 1',
        'SELECT a.id, (SELECT MAX(v) FROM m WHERE m.a_id = a.id) FROM a',
        'SELECT id FROM t ORDER BY id FETCH FIRST 10 ROWS ONLY',
    ],
)
def test_ordinary_selects_are_allowed(statement):
    verdict = SqlGuard.validate(statement)
    assert verdict.allowed is True, f'{statement!r} was rejected: {verdict.reason}'
    assert verdict.degraded is DEGRADE_NONE


# --- DaMeng: parsed with the oracle dialect, and it does parse ---------------


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT * FROM (SELECT id FROM t) WHERE ROWNUM <= 10',
        'SELECT a.id FROM a, b WHERE a.id = b.id(+)',
        'SELECT id FROM t START WITH parent_id IS NULL CONNECT BY PRIOR id = parent_id',
        'SELECT NVL(a, 0), DECODE(b, 1, 1, 0) FROM t',
        'SELECT SYSDATE FROM DUAL',
        'SELECT CAST(a AS VARCHAR2(10)) FROM t',
        'SELECT "字段名" FROM "表名"',
    ],
)
def test_dameng_syntax_is_allowed_and_not_degraded(statement):
    """DM is a target engine and has no sqlglot dialect. Measuring 27 DM/Oracle
    constructs showed they all parse under `oracle`, so a DM datasource must
    validate through the AST tier -- NOT by degrading to the narrow tier, which
    would silently weaken validation for the platform's most common engine."""
    verdict = SqlGuard.validate(statement, engine='dm')
    assert verdict.allowed is True, f'{statement!r} rejected: {verdict.reason}'
    assert verdict.degraded is DEGRADE_NONE, (
        f'{statement!r} degraded to the narrow tier; DM was expected to parse'
    )


# --- the degradation path (spec AC-20) ---------------------------------------


# Legitimate DM/Oracle queries sqlglot cannot parse. These are the real reason
# the narrow tier exists -- NOT syntax errors (see the module docstring for the
# `FOR UPDATE SKIP LOCKED` misdiagnosis that this list replaces).
UNPARSEABLE_BUT_LEGAL = [
    pytest.param(
        'SELECT * FROM t MODEL RETURN UPDATED ROWS DIMENSION BY (a) MEASURES (b) RULES (b[1] = 1)',
        id='dm-model-clause',
    ),
    pytest.param(
        'SELECT XMLAGG(XMLELEMENT(e, a)).getClobVal() FROM t MODEL DIMENSION BY (a) MEASURES (a m) RULES ()',
        id='oracle-xmlagg-model',
    ),
]


@pytest.mark.parametrize('statement', UNPARSEABLE_BUT_LEGAL)
def test_unparseable_statement_degrades_instead_of_being_rejected(statement):
    """AC-20: a parser failure must NOT become a rejection.

    These are legal queries the parser simply cannot read. Rejecting them would
    make the feature unusable on exactly the databases where the query language
    is richest.
    """
    verdict = SqlGuard.validate(statement, engine='dm')
    assert verdict.degraded is DEGRADE_PARSE_FAILED, (
        f'{statement!r} did not take the degraded path'
    )
    assert verdict.allowed is True, f'{statement!r} was rejected: {verdict.reason}'


@pytest.mark.parametrize('statement', UNPARSEABLE_BUT_LEGAL)
def test_degrading_does_not_wave_a_locking_read_through(statement):
    """The narrow tier must catch what the AST tier could not read.

    A locking read that fails to parse lands in the narrow tier, and if the
    narrow tier does not know the keyword, that lock is executed (AC-17)."""
    verdict = SqlGuard.validate(f'{statement} FOR UPDATE', engine='dm')
    assert verdict.degraded is DEGRADE_PARSE_FAILED, (
        f'{statement!r} + FOR UPDATE did not stay on the degraded path'
    )
    assert verdict.allowed is False, (
        'a locking read fell through the narrow tier and would reach the '
        'database (spec AC-17)'
    )


def test_skip_locked_is_caught_by_the_ast_tier_not_the_narrow_one():
    """The correction that replaced a wrong measurement.

    `FOR UPDATE SKIP LOCKED` parses (it was `SELECT ... FOR UPDATE SKIP LOCKED`
    -- with `...` as a column placeholder -- that failed, and the placeholder
    was the cause). It must therefore be rejected by the AST tier on its own
    merits, and must NOT be reported as a degradation."""
    verdict = SqlGuard.validate('SELECT * FROM t FOR UPDATE SKIP LOCKED')
    assert verdict.allowed is False
    assert verdict.degraded is DEGRADE_NONE, (
        'SKIP LOCKED parsed, so this is an AST-tier decision, not a degradation'
    )


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT * FROM t /* trailing',
        'SELECT * FROM (',
        'SELECT FROM WHERE',
        'SELECT ... FROM t',
    ],
)
def test_malformed_but_harmless_sql_is_passed_through(statement):
    """The guard is a SAFETY screen, not a syntax checker -- and that is the
    designed behaviour, worth stating because the opposite is the intuitive
    reading.

    AC-20 forbids rejecting a statement because the parser could not read it,
    and AC-37 lists "statement syntax error" as a failure to feed BACK to the
    model for correction. So a malformed statement that clears the safety
    screen is meant to reach the database, come back as an error, and enter the
    retry loop.

    Blocking unparseable input here would be worse than useless: the platform
    cannot tell "malformed" apart from "valid in a dialect I don't support", so
    rejecting would break exactly the queries AC-20 exists to protect."""
    verdict = SqlGuard.validate(statement)
    assert verdict.allowed is True, (
        f'{statement!r} was rejected; unparseable input must degrade, not be refused'
    )
    assert verdict.degraded is DEGRADE_PARSE_FAILED, 'the degradation must be recorded'


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT * FROM t /* trailing; DROP TABLE t',
        'SELECT * FROM ( DELETE FROM t',
        'SELECT FROM WHERE FOR UPDATE',
    ],
)
def test_malformed_sql_still_cannot_smuggle_a_write_through(statement):
    """The relaxed tier still enforces the whole policy: degrading relaxes the
    PARSER, never the rules. This is the property that actually has to hold --
    whatever we cannot read, we still refuse to write or lock."""
    assert SqlGuard.validate(statement).allowed is False, (
        f'{statement!r} was allowed through the narrow tier'
    )


# --- the narrow tier's own rules --------------------------------------------


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT * FROM t FOR UPDATE',
        'SELECT * FROM t LOCK IN SHARE MODE',
        'SELECT * FROM t FOR SHARE',
    ],
)
def test_narrow_tier_checks_locking_keywords(statement):
    """Exercised directly rather than only via the degradation path, so the
    narrow tier's keyword list is covered on its own terms."""
    verdict = SqlGuard._validate_narrow(statement)
    assert verdict.allowed is False, f'{statement!r} passed the narrow tier'


@pytest.mark.parametrize(
    'statement',
    [
        'DROP TABLE t',
        'DELETE FROM t',
        'UPDATE t SET a = 1',
        'INSERT INTO t VALUES (1)',
        'TRUNCATE TABLE t',
        'ALTER TABLE t ADD c INT',
        'GRANT SELECT ON t TO u',
    ],
)
def test_narrow_tier_rejects_write_keywords(statement):
    assert SqlGuard._validate_narrow(statement).allowed is False


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT 1',
        'SELECT * FROM t WHERE a = 1',
        'WITH x AS (SELECT 1) SELECT * FROM x',
    ],
)
def test_narrow_tier_allows_plain_selects(statement):
    assert SqlGuard._validate_narrow(statement).allowed is True


def test_narrow_tier_is_case_insensitive():
    """SQL keywords arrive in any case from a model, and identity comparisons
    are the easy way to miss `for update`."""
    assert SqlGuard._validate_narrow('select * from t for update').allowed is False
    assert SqlGuard._validate_narrow('SELECT 1').allowed is True


def test_narrow_tier_ignores_keywords_inside_literals():
    """A string literal containing 'DELETE' is data, not a statement.

    The narrow tier is deliberately conservative about *allowing* things -- but
    it must not reject a legitimate query merely because a column value or an
    alias contains a keyword."""
    verdict = SqlGuard._validate_narrow("SELECT id FROM t WHERE note = 'please DELETE this'")
    assert verdict.allowed is True, 'a keyword inside a string literal was treated as syntax'


# --- trailing semicolons -----------------------------------------------------


@pytest.mark.parametrize('statement', ['SELECT 1;', 'SELECT 1 ;  ', '  SELECT 1  '])
def test_a_single_statement_with_a_trailing_semicolon_is_allowed(statement):
    """Models emit a trailing semicolon constantly. Treating that as a second
    (empty) statement would reject almost every generated query."""
    assert SqlGuard.validate(statement).allowed is True


# --- empty and whitespace input ---------------------------------------------


@pytest.mark.parametrize('statement', ['', '   ', '\n\t '])
def test_empty_input_is_rejected(statement):
    """Nothing to execute is not a valid query; allowing it would push an empty
    statement at the driver."""
    verdict = SqlGuard.validate(statement)
    assert verdict.allowed is False


# --- the verdict carries what callers need ----------------------------------


def test_verdict_reports_which_tier_decided():
    """AC-20 requires the degradation to be observable and recorded, so the
    verdict has to say which tier ran -- a boolean would lose that."""
    parsed = SqlGuard.validate('SELECT 1')
    degraded = SqlGuard.validate(UNPARSEABLE_BUT_LEGAL[0].values[0], engine='dm')

    assert parsed.degraded is DEGRADE_NONE
    assert degraded.degraded is DEGRADE_PARSE_FAILED


def test_rejection_reason_never_echoes_the_statement():
    """The statement can contain schema and literal values. The reason is
    surfaced to callers and logged; it must describe the rule, not quote the
    input."""
    secret = 'SELECT * FROM customers WHERE card_no = \'4111111111111111\''
    verdict = SqlGuard.validate(secret + '; DROP TABLE t')

    assert verdict.allowed is False
    assert '4111111111111111' not in (verdict.reason or '')


def test_guard_is_usable_without_an_engine_argument():
    """Callers that do not know the engine (or have no datasource yet) still
    get validation; it defaults to the generic dialect."""
    assert SqlGuard.validate('SELECT 1').allowed is True


def test_verdict_is_a_dataclass_with_stable_fields():
    """The execution path records `degraded` on the query log; renaming it is a
    contract change, not a refactor."""
    verdict = SqlGuard.validate('SELECT 1')
    assert isinstance(verdict, GuardVerdict)
    assert hasattr(verdict, 'allowed') and hasattr(verdict, 'degraded')
