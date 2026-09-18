"""URI assembly for the six supported engines (spec AC-02 / AC-06).

The last section is the compatibility proof the Agent-node delegation rests on:
it rebuilds, from the same inputs, the exact strings the inlined
``_init_sql_address`` used to emit. Byte equality there is what makes "existing
inline-configured flows still work" (spec AC-54) a checked fact rather than an
assumption.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import URL, make_url

from bisheng.common.errcode.datasource import DatasourceUnsupportedEngineError
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services.credentials import encrypt_password
from bisheng.datasource.domain.services.engine_registry import (
    build_url,
    canonical_engine,
    connect_uri,
    from_datasource,
    safe_uri,
    spec_for,
    split_host_port,
)
from bisheng.workflow.nodes.agent.agent import SqlAgentParams

SECRET = "Sup3r$ecret@db/1"


def _url(engine: str, *, password: str = "pw", port: int | None = 1234) -> URL:
    return build_url(
        engine=engine,
        host="10.0.0.7",
        port=port,
        database="sales",
        username="app",
        password=password,
    )


# --- the six engines ---------------------------------------------------------


@pytest.mark.parametrize(
    "engine,drivername",
    [
        ("mysql", "mysql+pymysql"),
        ("postgresql", "postgresql+psycopg2"),
        ("gaussdb", "opengauss+psycopg2"),
        ("oracle", "oracle+oracledb"),
        ("db2", "db2+ibm_db"),
        ("dm", "dm+dmPython"),
    ],
)
def test_each_engine_gets_its_driver_prefix(engine, drivername):
    url = _url(engine)
    assert url.drivername == drivername
    assert connect_uri(url).startswith(f"{drivername}://")


def test_db2_uses_the_ibm_db_dialect():
    """AC-02 lists DB2. The code this replaces had a DB2 branch whose
    `try: pass / except ImportError: raise` could never fire, which is how the
    driver went missing without anyone noticing."""
    url = _url("db2")
    assert url.drivername == "db2+ibm_db"
    assert url.port == 1234


def test_dameng_engine_exists_at_all():
    """AC-02 lists 达梦 (DM8); the code this replaces had no DM branch and fell
    through to `raise ValueError`. 5236 is the DM default listener port."""
    url = _url("dm", port=None)
    assert url.drivername == "dm+dmPython"
    assert url.port == 5236
    assert canonical_engine("DM8") == "dm"


@pytest.mark.parametrize("spelling", ["postgres", "POSTGRESQL", " postgresql "])
def test_engine_spellings_normalise(spelling):
    """`postgres` is the spelling the Agent node's validator accepts and
    lowercases to; both must land on the same stored value."""
    assert canonical_engine(spelling) == "postgresql"


def test_oracle_carries_the_database_as_service_name():
    """Preserved from the legacy shape: the database name is a query parameter,
    not a path segment, so `oracle+oracledb://host:1521?service_name=ORCL`."""
    url = _url("oracle")
    assert url.database is None
    assert url.query["service_name"] == "sales"


def test_mysql_sets_utf8mb4():
    assert _url("mysql").query["charset"] == "utf8mb4"


# --- SQLite and anything else unsupported (AC-06) ----------------------------


@pytest.mark.parametrize("engine", ["sqlite", "SQLite", "sqlite3", "mongodb", "", "   ", None])
def test_unsupported_engine_is_rejected_with_the_module_error_code(engine):
    """SQLite is refused because its file path would become the datasource's
    permission boundary -- the platform authorizes datasources, not files."""
    with pytest.raises(DatasourceUnsupportedEngineError) as excinfo:
        build_url(
            engine=engine,  # type: ignore[arg-type]
            host="h",
            database="d",
            username="u",
            password="p",
        )
    assert excinfo.value.code == 11702


def test_rejection_message_does_not_echo_the_submitted_engine():
    """The engine string arrives from a request body. Echoing input back into
    an error is how F058 leaked PII, and the caller already knows what it sent."""
    with pytest.raises(DatasourceUnsupportedEngineError) as excinfo:
        spec_for("sqlite:///etc/passwd")
    assert "passwd" not in str(excinfo.value)


# --- credential handling in the URI ------------------------------------------


def test_password_with_reserved_characters_round_trips():
    """`@` in a password terminates the userinfo section of a hand-built URI;
    `/` starts the path. Both silently produce a malformed URI whose failure
    looks like a wrong password. URL.create percent-encodes them."""
    url = _url("mysql", password=SECRET)

    rendered = connect_uri(url)
    assert SECRET not in rendered, "the raw password must not survive in the URI text"
    parsed = make_url(rendered)
    assert parsed.password == SECRET
    assert parsed.host == "10.0.0.7"
    assert parsed.database == "sales"
    assert parsed.username == "app"


def test_percent_and_colon_in_password_round_trip():
    """`%` is the inverse hazard: a raw `%40` in a URI is decoded to `@`, so an
    unencoded password is silently *changed* rather than rejected."""
    secret = "a%b:c"
    assert make_url(connect_uri(_url("postgresql", password=secret))).password == secret


def test_from_datasource_decrypts_at_connect_time():
    row = DbDatasource(
        id=9,
        engine="dm",
        host="dm.internal",
        port=5236,
        database_name="SALES",
        username="app",
        password=encrypt_password(SECRET),
    )
    url = from_datasource(row)

    assert url.drivername == "dm+dmPython"
    assert make_url(connect_uri(url)).password == SECRET
    assert row.password != SECRET, "the stored column must stay ciphertext"


# --- the password must not escape through any rendering -----------------------


@pytest.mark.parametrize("engine", ["mysql", "postgresql", "gaussdb", "oracle", "db2", "dm"])
def test_no_rendering_of_the_url_contains_the_password(engine):
    """Startup logs, tracebacks and debugger dumps all render an object with
    str()/repr(). A URL that masks the password is safe in all three; a string
    would not be."""
    url = _url(engine, password=SECRET)

    # The old-style renderings are the point of this test, not a style slip.
    rendered_forms = (
        str(url),
        repr(url),
        safe_uri(url),
        f"{url}",
        "{}".format(url),  # noqa: UP032 - str.format is how loguru renders a value
        "%s" % (url,),  # noqa: UP031 - named-placeholder rendering, logged nowhere else
    )
    for rendered in rendered_forms:
        assert SECRET not in rendered, rendered
        assert "***" in rendered


def test_url_survives_a_loguru_render():
    """loguru formats with str.format and renders objects through str(), so
    `{}` is the rendering that matters most in production."""
    url = _url("mysql", password=SECRET)
    assert SECRET not in "{!r}".format(url)  # noqa: UP032 - see above


def test_bare_percent_formatting_a_url_fails_rather_than_leaking():
    """`URL` is a NamedTuple, so `'%s' % url` unpacks it as a tuple and raises
    instead of rendering. Loud is the right failure for a rendering path nobody
    audited -- pinned here so a future change to that base class cannot quietly
    turn it into a plaintext rendering. (The parenthesised form, `'%s' % (url,)`,
    is a single argument and does render -- masked, as the test above asserts.)
    """
    with pytest.raises(TypeError):
        "%s" % _url("mysql", password=SECRET)  # noqa: UP031 - the point is the bare % operator


# --- the legacy Agent-node path (spec AC-54) ---------------------------------


def test_split_host_port_handles_the_shapes_the_old_code_passed_through():
    assert split_host_port("10.0.0.7:3306") == ("10.0.0.7", 3306)
    assert split_host_port(" 10.0.0.7 ") == ("10.0.0.7", None)
    # Not a port: the old code pasted it into the URI and let the driver fail,
    # so it stays part of the host rather than becoming a new hard failure.
    assert split_host_port("db.host:notaport") == ("db.host:notaport", None)
    assert split_host_port("[::1]:5432") == ("::1", 5432)


@pytest.mark.parametrize(
    "engine,address,database,expected",
    [
        (
            "mysql",
            "10.0.0.7:3306",
            "sales",
            "mysql+pymysql://app:pw@10.0.0.7:3306/sales?charset=utf8mb4",
        ),
        ("db2", "10.0.0.7:50000", "sales", "db2+ibm_db://app:pw@10.0.0.7:50000/sales"),
        (
            "postgres",
            "10.0.0.7:5432",
            "sales",
            "postgresql+psycopg2://app:pw@10.0.0.7:5432/sales",
        ),
        (
            "postgresql",
            "10.0.0.7:5432",
            "sales",
            "postgresql+psycopg2://app:pw@10.0.0.7:5432/sales",
        ),
        (
            "gaussdb",
            "10.0.0.7:8000",
            "sales",
            "opengauss+psycopg2://app:pw@10.0.0.7:8000/sales",
        ),
        (
            "oracle",
            "10.0.0.7:1521",
            "ORCL",
            "oracle+oracledb://app:pw@10.0.0.7:1521?service_name=ORCL",
        ),
    ],
)
def test_legacy_five_engine_uris_are_byte_identical(engine, address, database, expected):
    """The strings `_init_sql_address` produced before it delegated here, with a
    plain password (the only shape that ever worked). Any difference here is a
    change to the behaviour of existing inline-configured flows."""
    host, port = split_host_port(address)
    url = build_url(
        engine=engine,
        host=host,
        port=port,
        database=database,
        username="app",
        password="pw",
    )
    assert connect_uri(url) == expected


# --- the delegated Agent-node method -----------------------------------------

# The format strings the inlined implementation used, copied from the version
# this change replaced. Kept as a golden rather than re-derived from the new
# code, so the test still fails if both drift together.
_LEGACY_TEMPLATES = {
    "mysql": "mysql+pymysql://{u}:{p}@{a}/{d}?charset=utf8mb4",
    "db2": "db2+ibm_db://{u}:{p}@{a}/{d}",
    "postgres": "postgresql+psycopg2://{u}:{p}@{a}/{d}",
    "postgresql": "postgresql+psycopg2://{u}:{p}@{a}/{d}",
    "gaussdb": "opengauss+psycopg2://{u}:{p}@{a}/{d}",
    "oracle": "oracle+oracledb://{u}:{p}@{a}?service_name={d}",
}

_ADDRESS = "10.0.0.7:3306"


class _QueryOnlyNode:
    """`AgentNode._init_sql_address` reads exactly one attribute, so binding the
    real method to this stub exercises the real delegation. Standing up an
    AgentNode needs a graph, a tenant and an LLM, and would test none of them.
    """

    def __init__(self, params):
        self._sql_agent = params


def _params(**overrides):
    values = {
        "database_engine": "mysql",
        "db_username": "app",
        "db_password": "pw",
        "db_address": _ADDRESS,
        "db_name": "sales",
        "open": True,
    }
    values.update(overrides)
    return SqlAgentParams(**values)


@pytest.mark.parametrize("engine", sorted(_LEGACY_TEMPLATES))
def test_delegated_method_reproduces_the_replaced_implementation(engine):
    """AC-54: an existing flow with inline connection info keeps working. It
    does so because the emitted string is unchanged, not because the old code
    happens to still be there."""
    from bisheng.workflow.nodes.agent.agent import AgentNode

    node = _QueryOnlyNode(_params(database_engine=engine))
    expected = _LEGACY_TEMPLATES[engine].format(u="app", p="pw", a=_ADDRESS, d="sales")

    assert AgentNode._init_sql_address(node) == expected


def test_delegated_method_returns_empty_without_an_sql_agent():
    from bisheng.workflow.nodes.agent.agent import AgentNode

    assert AgentNode._init_sql_address(_QueryOnlyNode(None)) == ""


def test_delegated_method_serves_dameng_where_the_old_one_raised():
    """The old method had no DM branch at all (spec AC-02 lists it). The port is
    filled from the engine's default, since `db_address` carries none here.

    `model_construct` skips validation on purpose: `SqlAgentParams`'
    `validate_database_engine` still rejects `dm` outright, so today a DaMeng
    datasource cannot be chosen through the legacy inline node params at all.
    That is the Agent-node convergence's to open up (AC-53/AC-56); this pins
    the half that is done -- once a `dm` value reaches the method, it works.
    """
    from bisheng.workflow.nodes.agent.agent import AgentNode

    params = SqlAgentParams.model_construct(
        database_engine="dm",
        db_username="app",
        db_password="pw",
        db_address="dm.internal",
        db_name="SALES",
        open=True,
    )
    node = _QueryOnlyNode(params)

    assert AgentNode._init_sql_address(node) == "dm+dmPython://app:pw@dm.internal:5236/SALES"


def test_legacy_param_validator_still_rejects_the_new_engines():
    """Noted so the gap above cannot be mistaken for an oversight: an inline
    `sql_agent` node cannot be configured with DaMeng, and no existing flow
    changes because of it (spec AC-54 holds in both directions)."""
    from bisheng.workflow.nodes.agent.agent import SqlAgentParams

    with pytest.raises(ValidationError):
        SqlAgentParams(
            database_engine="dm",
            db_username="app",
            db_password="pw",
            db_address=_ADDRESS,
            db_name="sales",
            open=True,
        )


def test_delegated_method_raises_the_module_error_code_for_an_unknown_engine():
    """`SqlAgentParams` rejects most unknown engines first; an empty value slips
    past its validator, which is how the old code reached its bare `raise
    ValueError`. That path now carries error code 11702 so callers can branch."""
    from bisheng.workflow.nodes.agent.agent import AgentNode

    with pytest.raises(DatasourceUnsupportedEngineError) as excinfo:
        AgentNode._init_sql_address(_QueryOnlyNode(_params(database_engine="")))
    assert excinfo.value.code == 11702
