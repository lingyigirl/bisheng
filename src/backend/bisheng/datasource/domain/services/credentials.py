"""Datasource credential handling (spec AC-04).

Three rules, and this module exists to make them hard to break:

1. **One crypto path.** Encryption and decryption call the project's existing
   Fernet helpers -- ``encrypt_token`` / ``decrypt_token`` in
   ``core/config/settings.py`` -- the same ones ``gpu_manage`` and ``org_sync``
   use. A second implementation would mean a second place for the key and the
   cipher to drift apart.
2. **Plaintext has a lifetime, not a storage location.** It exists between the
   decrypt call and the driver's connect call and nowhere else. Decryption
   happens *per connect*, not when a row is loaded, so a list or detail
   response never has one in memory to accidentally serialize.
3. **Serialization is deny-by-default.** :func:`to_safe_dict` drops every field
   whose *name* looks like a credential instead of naming the fields it keeps.
   F058 leaked a field that was added after its redaction list was written; a
   keep-list has exactly that failure mode, so this one is a drop-list keyed on
   the name rather than on a hand-maintained set of field names.

The row's ``password`` column holds ciphertext produced by
:func:`encrypt_password`; nothing in this module ever puts a plaintext password
in a log line or an exception message.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cryptography.fernet import InvalidToken
from loguru import logger

from bisheng.common.errcode.datasource import DatasourceConnectFailedError
from bisheng.core.config.settings import decrypt_token, encrypt_token

# What a masked credential renders as. Only ever shown in place of a value the
# caller already knows, never as a stand-in for a field in a response -- see
# `to_safe_dict`, which drops the field and reports presence instead.
MASK = "********"

# Substrings that make a field name sensitive. Matched as substrings so that
# `password`, `password_encrypted`, `db_password` and `password_hash` are all
# covered without being enumerated. Deliberately over-inclusive: a false
# positive hides a harmless field from a response (loud, easily noticed), a
# false negative leaks a credential (silent).
_SENSITIVE_NAME_PARTS = ("password", "passwd", "secret", "token", "credential", "api_key")


def is_sensitive_field(name: str) -> bool:
    """Whether a field with this name may not be serialized."""
    lowered = name.lower()
    return any(part in lowered for part in _SENSITIVE_NAME_PARTS)


def encrypt_password(plaintext: str | None) -> str:
    """Encrypt for storage in the ``password`` column.

    Returns ``str``: ``encrypt_token`` returns bytes and the column is text
    (same conversion as ``gpu_manage``'s ``encrypt_password``).
    """
    return encrypt_token(plaintext or "").decode()


def decrypt_password(ciphertext: str | None, *, datasource_id: int | None = None) -> str:
    """Decrypt at connect time.

    A failure here means the row cannot be used at all (the Fernet key changed
    under it, or the value was written by something other than
    :func:`encrypt_password`). It is reported as a connection failure, which is
    what it is from the caller's point of view, and never as a validation error
    carrying the value.

    The ciphertext is deliberately absent from the log line: a log is a read
    path, and spec AC-04 says read paths do not echo credentials. The
    datasource id is enough to identify the row.
    """
    try:
        return decrypt_token(ciphertext or "")
    except (InvalidToken, TypeError, ValueError):
        logger.opt(exception=True).error(
            "datasource credential could not be decrypted (datasource_id={})", datasource_id
        )
        raise DatasourceConnectFailedError()


def redact(text: str | None, *, secrets: Iterable[str | None] = ()) -> str:
    """Replace every occurrence of ``secrets`` in ``text`` with :data:`MASK`.

    For driver error messages, which are free-form strings nobody screens.
    ``psycopg2`` reports a bad password as ``connection to server at "db" ...``
    but several drivers (and every DSN-echoing wrapper) put the whole
    connection string, password included, into the same message. The caller
    knows the password; it is the one thing that must not survive.
    """
    if not text:
        return ""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, MASK)
    return out


def to_safe_dict(row: Any) -> dict[str, Any]:
    """Serialize a datasource row for a response, a log or an audit trail.

    Credential-named fields are removed and replaced by a single
    ``has_password`` flag, so a caller can still render "a password is set"
    without ever receiving the value.

    Takes a SQLModel row or a plain mapping. F061's create/read endpoints must
    build their responses through this rather than listing fields by hand:
    listing fields is how a column added later reaches the client unredacted.
    """
    data = row.model_dump() if hasattr(row, "model_dump") else dict(row)
    safe = {key: value for key, value in data.items() if not is_sensitive_field(key)}
    safe["has_password"] = any(value for key, value in data.items() if is_sensitive_field(key))
    return safe
