"""Connection-URI assembly for the supported engines (spec AC-02).

One source of truth. The workflow Agent node's ``_init_sql_address`` used to
inline this logic; it now delegates here, so the datasource module and the
legacy inline-config path cannot drift apart.

Two gaps in the logic this replaces, both of which the spec requires closing:

  * **DaMeng had no branch at all.** The old implementation covered five
    engines and fell through to ``else: raise ValueError``; AC-02 lists six,
    DM8 included. ``dm+dmPython://`` (default port 5236) is verified loadable
    against the installed dialect.
  * **The DB2 branch was dead code.** Its ``try: pass / except ImportError:
    raise`` could never fire, so the missing ``ibm_db`` driver stayed invisible
    until connection time. The driver is now a declared dependency.

**SQLite is rejected** (spec AC-06), and not merely as "not in the list": SQLite
is a file path rather than a server, so the path itself would become the
datasource's permission boundary -- the wrong shape for a platform whose
authorization is per-datasource (INV-19).

**Credentials.** The stored password is Fernet ciphertext (``credentials.py``);
it is decrypted here, at connect time, and the result is a SQLAlchemy
:class:`~sqlalchemy.engine.URL` rather than a string. That choice is the whole
leak defence: ``str(url)`` and ``repr(url)`` render the password as ``***``, so
a URL that escapes into a log line, a traceback or a debugger dump is already
safe (verified, not assumed -- see ``test_engine_registry.py``). The password is
still recoverable, but only by asking for it by name: :func:`connect_uri`.
Anything that can take a URL should take the URL -- ``create_engine`` does.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.engine import URL

from bisheng.common.errcode.datasource import DatasourceUnsupportedEngineError
from bisheng.datasource.domain.models.datasource import (
    DAMENG,
    DB2,
    GAUSSDB,
    MYSQL,
    ORACLE,
    POSTGRESQL,
    DbDatasource,
)
from bisheng.datasource.domain.services.credentials import decrypt_password


@dataclass(frozen=True)
class EngineSpec:
    """Everything engine-specific about opening a connection.

    ``connect_args`` is here rather than at the call site because the DBAPI
    keyword for a connect timeout differs per driver (``connect_timeout`` for
    pymysql and psycopg2, ``tcp_connect_timeout`` for oracledb). Keying it on
    the engine keeps that knowledge next to the driver name it belongs to.
    """

    engine: str
    drivername: str
    default_port: int
    # Some engines carry the database name as a query parameter instead of a
    # URI path segment. Oracle is the one that matters here: the legacy code
    # produced `oracle+oracledb://host:1521?service_name=ORCL`, and that shape
    # is preserved rather than normalised into a path.
    database_in_query: str | None = None
    query: Mapping[str, str] = field(default_factory=dict)
    connect_args: Mapping[str, Any] = field(default_factory=dict)
    # Alternative spellings accepted on input, normalised to `engine`.
    aliases: tuple[str, ...] = ()


_SPECS: tuple[EngineSpec, ...] = (
    EngineSpec(
        MYSQL,
        "mysql+pymysql",
        3306,
        # Matches the charset the legacy code always set. Without it a client
        # connection can land on latin1 and mangle non-ASCII data.
        query={"charset": "utf8mb4"},
        connect_args={"connect_timeout": 10},
    ),
    EngineSpec(
        POSTGRESQL,
        "postgresql+psycopg2",
        5432,
        connect_args={"connect_timeout": 10},
        aliases=("postgres",),
    ),
    # GaussDB speaks the openGauss wire protocol; `opengauss+psycopg2` is the
    # dialect the legacy code used. openGauss' own default port is 5432 (some
    # commercial GaussDB deployments publish 8000) -- the port is a required
    # column on the datasource, so this default only applies when one is absent.
    EngineSpec(GAUSSDB, "opengauss+psycopg2", 5432, connect_args={"connect_timeout": 10}),
    EngineSpec(
        ORACLE,
        "oracle+oracledb",
        1521,
        database_in_query="service_name",
        connect_args={"tcp_connect_timeout": 10},
    ),
    # ibm_db takes no connect-timeout keyword through `connect_args`; the probe
    # bounds it from the outside instead.
    EngineSpec(DB2, "db2+ibm_db", 50000),
    # dmPython likewise. `dm8` and `dameng` are accepted because the spec names
    # the engine DM8, so any of the three is a plausible value in a request body.
    EngineSpec(DAMENG, "dm+dmPython", 5236, aliases=("dm8", "dameng")),
)

ENGINE_SPECS: dict[str, EngineSpec] = {}
for _spec in _SPECS:
    ENGINE_SPECS[_spec.engine] = _spec
    for _alias in _spec.aliases:
        ENGINE_SPECS[_alias] = _spec
del _spec, _alias


def spec_for(engine: str | None) -> EngineSpec:
    """The spec for ``engine``, or raise the unsupported-engine error.

    This is the single rejection point for spec AC-06: ``sqlite`` is not in the
    table, so it is refused by the same path as any other unknown engine, with
    error code 11702.
    """
    spec = ENGINE_SPECS.get((engine or "").strip().lower())
    if spec is None:
        # The engine name is not a credential and naming it in the log is what
        # makes a misconfiguration diagnosable. The caller-visible message stays
        # generic (error code 11702 carries none of the input).
        logger.warning("rejected unsupported datasource engine {!r}", engine)
        raise DatasourceUnsupportedEngineError()
    return spec


def canonical_engine(engine: str | None) -> str:
    """Normalise a spelling to the value stored in ``db_datasource.engine``."""
    return spec_for(engine).engine


def split_host_port(address: str) -> tuple[str, int | None]:
    """Split the legacy ``host:port`` free-text field into its two parts.

    The Agent node stores both in one string (``db_address``) and the old code
    pasted that string into the URI verbatim, which is no longer possible: a URL
    built with ``host='h:1521'`` renders as ``[h:1521]``, because SQLAlchemy
    reads a colon in the host as an IPv6 literal and brackets it. Splitting
    keeps the emitted URI identical for every value the old path accepted.

    A trailing segment that is not all digits leaves the whole string as the
    host, reproducing the old pass-through behaviour rather than failing on
    input the old code tolerated.
    """
    text = (address or "").strip()
    host, separator, port_text = text.rpartition(":")
    if separator and host and port_text.isdigit():
        # `make_url` returns IPv6 hosts unbracketed, so a bracketed literal in
        # the input has to be unwrapped before it can round-trip.
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host, int(port_text)
    return text, None


def build_url(
    *,
    engine: str,
    host: str,
    database: str,
    username: str,
    password: str,
    port: int | None = None,
) -> URL:
    """Assemble the connect URL. Never returns a string.

    The password is passed through as-is: ``URL.create`` percent-encodes it,
    which is what makes a password containing ``@``, ``/`` or ``:`` survive the
    trip. Interpolating it into a URI by hand (what this replaces) silently
    produces a malformed URI for any of those characters, and the failure
    surfaces as "wrong password" rather than as a parse error.
    """
    spec = spec_for(engine)

    query = dict(spec.query)
    db_name: str | None = database
    if spec.database_in_query:
        query[spec.database_in_query] = database
        db_name = None

    # An absent host means "unspecified", so no default port is filled in for
    # it: a port with no host renders as `@:3306`, which is not a URI the
    # legacy path ever produced for the same (unconnectable) input.
    resolved_port = port or (spec.default_port if host else None)

    return URL.create(
        drivername=spec.drivername,
        username=username,
        password=password,
        host=host,
        port=resolved_port,
        database=db_name,
        query=query,
    )


def from_datasource(datasource: DbDatasource) -> URL:
    """Assemble the connect URL for a stored row.

    The only place ciphertext becomes plaintext, and it happens per connect:
    a row loaded for a list response never carries a usable password.
    """
    return build_url(
        engine=datasource.engine,
        host=datasource.host,
        port=datasource.port,
        database=datasource.database_name,
        username=datasource.username,
        password=decrypt_password(datasource.password, datasource_id=datasource.id),
    )


def safe_uri(url: URL) -> str:
    """A log-safe rendering of a URL. Cannot contain the password.

    Prefer :func:`str` directly with loguru -- this exists so the intent is
    visible at the call site.
    """
    return url.render_as_string(hide_password=True)


def connect_uri(url: URL) -> str:
    """Render the URI **with** the password, for a caller that needs a string.

    Named deliberately so that it is greppable and obviously exceptional: every
    other rendering of a URL masks the password. The only caller is the legacy
    Agent node, whose ``SqlAgentAPIWrapper.sql_address`` is a pydantic ``str``
    field and therefore rejects a :class:`URL` outright. New code should pass
    the URL itself -- ``create_engine``, ``make_url`` and the DAO layer all
    accept one.
    """
    return url.render_as_string(hide_password=False)


_dll_path_registered = False


def ensure_db2_driver_loadable() -> None:
    """Make ``import ibm_db`` work on Windows. No-op everywhere else.

    Measured, on Windows with the pinned ``ibm-db 3.2.7``: the win_amd64 wheel
    installs fine but ``import ibm_db`` then fails with ``ImportError: DLL load
    failed ... The specified module could not be found``. The extension module
    needs the DLLs vendored in its own ``clidriver/bin`` on the *DLL search
    path*, and Python 3.8+ stopped searching the PATH for an extension module's
    dependencies. Registering that directory explicitly is the documented fix,
    and it is the difference between DB2 working and not working here --
    verified both ways against the installed wheel.

    Deliberately lazy and idempotent: registered at most once, only when a DB2
    connection is actually attempted, and failure degrades quietly rather than
    raising. A machine that never touches DB2 should not pay for this, and a
    missing ``clidriver`` should surface as a normal driver connection error
    rather than as an error from here.

    Not needed for ``ibm-db 3.3.0``, which loads without it -- but that version
    is deliberately not pinned; see the note in ``pyproject.toml``.
    """
    global _dll_path_registered
    if _dll_path_registered or sys.platform != 'win32':
        return

    clidriver_bin = Path(sys.prefix) / 'Lib' / 'site-packages' / 'clidriver' / 'bin'
    try:
        if clidriver_bin.is_dir() and hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(str(clidriver_bin))
    except OSError:
        # Fall through: the subsequent import raises the real, more
        # informative error.
        pass
    # Latched even on failure, so one clear import error is not turned into a
    # per-connection log line.
    _dll_path_registered = True
