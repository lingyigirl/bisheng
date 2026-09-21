"""Term library errors, module code 115 (F060).

Registered in the v2.7.0 release contract's module table. Six codes, one per
failure the feature can report to an admin — the library is maintained by hand,
so every rejection has to say which rule it broke rather than "invalid input".

Deliberately NOT here: anything about the conversation side. An injection
failure never surfaces as an error — the term block is an enhancement, and a
failure to build one degrades to "no terms this turn" (design §7.4). Turning
that into an error code would make a usable conversation fail.
"""

from .base import BaseErrorCode


class TermNotFoundError(BaseErrorCode):
    """The term an operation targeted does not exist.

    Also the answer for "exists but you may not see it" — the same conflation
    the datasource module uses. Telling those apart would let a caller probe
    which ids exist in another tenant (INV-16).
    """

    Code: int = 11501
    Msg: str = "Term not found"


class TermDefinitionInvalidError(BaseErrorCode):
    """The term's definition breaks a rule (spec AC-04).

    Covers all four: empty standard term, empty definition, an over-long
    definition (>200 chars), and an illegal alias (not a string, or blank).
    One code rather than four because the caller fixes them in the same editor —
    the message names the specific field.
    """

    Code: int = 11502
    Msg: str = "Invalid term definition"


class TermDuplicateError(BaseErrorCode):
    """The standard term, or one of the aliases, collides with an existing term.

    Uniqueness is per tenant and spans BOTH fields (spec AC-02 / AC-03), which
    is why this is one code: an alias colliding with another term's alias and a
    standard term colliding with another's standard term are the same class of
    problem to an admin, and matching either one makes the match ambiguous.
    """

    Code: int = 11503
    Msg: str = "Duplicate term or alias"


class TermLibraryLimitExceededError(BaseErrorCode):
    """The library is at its cap (spec AC-06, 500 terms)."""

    Code: int = 11504
    Msg: str = "Term library size limit exceeded"


class TermImportFileParseError(BaseErrorCode):
    """An uploaded import file could not be parsed (spec AC-09).

    F060's own code, not F059's 11301, even though both wrap the same shared
    parser: the parse core is a shared utility, and each feature reports its
    failures in its own module. Reusing 11301 would put a platform-library code
    on a tenant-facing flow.
    """

    Code: int = 11505
    Msg: str = "Import failed: the file could not be parsed or contains no valid terms"


class TermDepartmentNotFoundError(BaseErrorCode):
    """The referenced department does not exist, or was archived (spec AC-15).

    The term's department is display metadata (INV-17), so an archived department
    is NOT an error at read time — this is raised only when one is explicitly
    assigned on write.
    """

    Code: int = 11506
    Msg: str = "Department not found or archived"
