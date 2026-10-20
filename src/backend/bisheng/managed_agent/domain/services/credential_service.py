"""F078 outbound credential storage (encrypt at rest, mask on read).

The credential is one opaque Fernet blob in ``managed_agent.credential_ciphertext``
(``LargeText``), matching the F061 datasource precedent. It is decrypted only at
call time; responses expose ``has_credential``/``credential_type`` and a fixed
mask, never the secret or headers.

Known platform risk (recorded in design 决策 8): the Fernet key is the existing
hardcoded shared constant in ``core/config/settings.py``. F078 reuses it rather
than introducing a second key, and does not add plaintext secrets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bisheng.common.errcode.managed_agent import ManagedAgentCredentialInvalidError
from bisheng.core.config.settings import decrypt_token, encrypt_token
from bisheng.managed_agent.domain.models.managed_agent import CredentialType

MASKED_SECRET = "******"


@dataclass(frozen=True)
class CredentialPayload:
    auth_type: str
    secret: str
    headers: dict[str, str]


def _validate(auth_type: str, secret: str | None, headers: dict | None) -> CredentialPayload:
    if auth_type not in {item.value for item in CredentialType}:
        raise ManagedAgentCredentialInvalidError(msg="unsupported auth_type")
    if auth_type == CredentialType.NONE.value:
        return CredentialPayload(auth_type=auth_type, secret="", headers={})
    if not secret:
        raise ManagedAgentCredentialInvalidError(msg="secret is required")
    if headers is not None and not isinstance(headers, dict):
        raise ManagedAgentCredentialInvalidError(msg="headers must be an object")
    clean: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ManagedAgentCredentialInvalidError(msg="headers must be string pairs")
        if "\n" in key or "\n" in value or "\r" in key or "\r" in value:
            raise ManagedAgentCredentialInvalidError(msg="header injection rejected")
        clean[key] = value
    return CredentialPayload(auth_type=auth_type, secret=secret, headers=clean)


def encrypt_credential(
    auth_type: str,
    secret: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, str, bool]:
    """Return (ciphertext, credential_type, has_credential)."""
    payload = _validate(auth_type, secret, headers)
    if payload.auth_type == CredentialType.NONE.value:
        return "", payload.auth_type, False
    raw = json.dumps(
        {"auth_type": payload.auth_type, "secret": payload.secret, "headers": payload.headers},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # encrypt_token returns bytes; the DB column is text (same as F061/org_sync).
    return encrypt_token(raw).decode(), payload.auth_type, True


def decrypt_credential(ciphertext: str | None) -> CredentialPayload:
    if not ciphertext:
        return CredentialPayload(auth_type=CredentialType.NONE.value, secret="", headers={})
    try:
        token = ciphertext.encode() if isinstance(ciphertext, str) else ciphertext
        raw = decrypt_token(token)
        data: Any = json.loads(raw)
        return CredentialPayload(
            auth_type=str(data.get("auth_type") or CredentialType.NONE.value),
            secret=str(data.get("secret") or ""),
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
        )
    except Exception as exc:
        raise ManagedAgentCredentialInvalidError(msg="credential cannot be decrypted") from exc


def mask_credential(credential_type: str, has_credential: bool) -> str | None:
    """The only credential-related value a read response may expose."""
    if not has_credential or credential_type == CredentialType.NONE.value:
        return None
    return MASKED_SECRET
