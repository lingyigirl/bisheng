"""Credential encryption and non-echo (spec AC-04).

The load-bearing case is `test_serializing_the_whole_row_leaks_nothing`: it
serializes the response object as a whole and searches the resulting text for
the secret, rather than asserting on fields by name. F058 leaked PII because a
field added after the redaction list was written was never checked by a
field-by-field test; a whole-object search catches the addition automatically.
"""

import json

import pytest

from bisheng.common.errcode.datasource import DatasourceConnectFailedError
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services.credentials import (
    MASK,
    decrypt_password,
    encrypt_password,
    is_sensitive_field,
    redact,
    to_safe_dict,
)

SECRET = "Sup3r$ecret@db/1"


def _row(**overrides) -> DbDatasource:
    values = {
        "id": 3,
        "tenant_id": 1,
        "user_id": 2,
        "name": "sales-db",
        "engine": "mysql",
        "host": "10.0.0.7",
        "port": 3306,
        "database_name": "sales",
        "username": "app",
        "password": encrypt_password(SECRET),
        "description": "报表库",
    }
    values.update(overrides)
    return DbDatasource(**values)


# --- round trip --------------------------------------------------------------


def test_encrypt_decrypt_round_trip():
    ciphertext = encrypt_password(SECRET)
    assert ciphertext != SECRET
    assert SECRET not in ciphertext
    assert decrypt_password(ciphertext) == SECRET


def test_empty_password_round_trips():
    assert decrypt_password(encrypt_password("")) == ""


def test_undecryptable_value_raises_a_connection_error_not_the_value():
    """A row written by something other than `encrypt_password` (or a rotated
    Fernet key) is not usable. The failure must name the row, never the value --
    an exception message is a read path like any other."""
    with pytest.raises(DatasourceConnectFailedError) as excinfo:
        decrypt_password("not-a-fernet-token", datasource_id=42)
    assert excinfo.value.code == 11701
    assert "not-a-fernet-token" not in str(excinfo.value)


# --- serialization -----------------------------------------------------------


def test_serializing_the_whole_row_leaks_nothing():
    """Serialize the entire response object and search the text for both the
    plaintext and the stored ciphertext."""
    row = _row()
    payload = json.dumps(to_safe_dict(row), default=str, ensure_ascii=False)

    assert SECRET not in payload, payload
    assert row.password not in payload, payload
    assert '"password"' not in payload, payload
    assert json.loads(payload)["has_password"] is True


def test_serialization_reports_the_absence_of_a_password():
    assert to_safe_dict(_row(password=""))["has_password"] is False


@pytest.mark.parametrize(
    "field",
    ["password", "db_password", "password_hash", "password_encrypted", "api_secret", "access_token"],
)
def test_fields_named_like_a_credential_are_dropped_without_being_listed(field):
    """The F058 failure mode: a field added later, after the redaction list was
    written. Matching on the name means a new one is covered on arrival."""
    payload = to_safe_dict({"id": 1, field: "leaky-value"})
    assert field not in payload
    assert "leaky-value" not in json.dumps(payload)


def test_non_credential_fields_survive_serialization():
    """The drop-list must not eat the response: a name-based rule that is too
    broad silently empties the payload."""
    payload = to_safe_dict(_row())
    assert payload["name"] == "sales-db"
    assert payload["host"] == "10.0.0.7"
    assert payload["engine"] == "mysql"
    assert payload["description"] == "报表库"


def test_sensitive_name_matching_is_case_insensitive():
    assert is_sensitive_field("DB_Password")
    assert not is_sensitive_field("port")


# --- driver error scrubbing --------------------------------------------------


def test_redact_removes_the_password_from_a_driver_message():
    """Several drivers echo the whole DSN in an authentication error, and that
    text is otherwise a free-form string nobody screens before it reaches the
    caller."""
    message = f'FATAL: connection to "db" failed for user "app" (dsn=host=db password={SECRET})'
    cleaned = redact(message, secrets=[SECRET])
    assert SECRET not in cleaned
    assert MASK in cleaned
    assert 'connection to "db" failed' in cleaned, "the useful part is kept"


def test_redact_tolerates_missing_values():
    assert redact(None, secrets=[SECRET]) == ""
    assert redact("plain message", secrets=[None, ""]) == "plain message"
