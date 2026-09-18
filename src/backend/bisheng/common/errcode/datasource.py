from .base import BaseErrorCode


# Datasource / database Q&A module error codes, module code: 117.
# Allocation checked against all three sources before claiming (see the
# v2.7.0 release-contract): common/errcode/*.py, every version's
# release-contract, and the frontend api_errors JSON. 114 is a frontend-only
# 403 sentinel and 115 belongs to v2.8.0's term library.
class DatasourceConnectFailedError(BaseErrorCode):
    """Test-connection or query-time connection failed.

    The message is deliberately generic: the underlying driver error can carry
    the host, port, account or DSN, none of which may reach the caller.
    """

    Code: int = 11701
    Msg: str = "Failed to connect to the database with the configured settings"


class DatasourceUnsupportedEngineError(BaseErrorCode):
    """Engine is not one of the supported six (MySQL / PostgreSQL / GaussDB /
    Oracle / DB2 / DM8). SQLite is rejected by the same path -- its file path
    would become an implicit permission boundary.
    """

    Code: int = 11702
    Msg: str = "Unsupported database engine"


class DatasourceSchemaCollectFailedError(BaseErrorCode):
    """Listing tables or collecting a table-structure snapshot failed."""

    Code: int = 11703
    Msg: str = "Failed to collect the database schema"


class DatasourceStatementRejectedError(BaseErrorCode):
    """The platform's SQL guard refused the statement: multiple statements,
    a non-query, a write/DDL/authorization change, or a locking read.
    See the datasource design's SQL-validation decision.
    """

    Code: int = 11704
    Msg: str = "The statement was rejected by the query safety policy"


class DatasourceStatementExecutionFailedError(BaseErrorCode):
    """The database rejected the statement at execution time.

    Callers that feed the failure back to the model must pass the database's
    own message; this default is the user-facing fallback and stays free of
    internal structure, connection details and stack traces.
    """

    Code: int = 11705
    Msg: str = "The query could not be executed"


class DatasourceExecutionTimeoutError(BaseErrorCode):
    """The query exceeded the execution timeout."""

    Code: int = 11706
    Msg: str = "The query timed out"


class DatasourceRowLimitExceededError(BaseErrorCode):
    """The result set exceeded the row limit.

    Not a hard failure: the caller answers with the rows already fetched and
    marks the result as truncated, and the query does NOT enter the retry
    path. Kept as an error code so the truncation is observable to callers
    that prefer an explicit signal.
    """

    Code: int = 11707
    Msg: str = "The result was truncated at the row limit"


class DatasourceNotFoundError(BaseErrorCode):
    """The datasource does not exist in this tenant, or the caller is not
    authorized for it.

    Both cases share one code on purpose: distinguishing them would let a
    caller probe which datasource ids exist in another tenant.
    """

    Code: int = 11708
    Msg: str = "Datasource not found or access denied"


class DatasourceNameDuplicateError(BaseErrorCode):
    """A datasource with this name already exists in the current tenant."""

    Code: int = 11709
    Msg: str = "A datasource with this name already exists"


class DatasourceModelSlotNotConfiguredError(BaseErrorCode):
    """Raised only where a slot is genuinely required.

    SQL generation does NOT use this: an unconfigured SQL-generation slot
    falls back to the conversation model without error. This code is for
    paths that cannot proceed without an explicit slot.
    """

    Code: int = 11710
    Msg: str = "No model is configured for this operation"
