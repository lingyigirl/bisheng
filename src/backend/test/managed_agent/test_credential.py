"""F078 T-13: credential encryption, masking and validation."""

import pytest

from bisheng.common.errcode.managed_agent import ManagedAgentCredentialInvalidError
from bisheng.managed_agent.domain.models.managed_agent import CredentialType
from bisheng.managed_agent.domain.services.credential_service import (
    MASKED_SECRET,
    decrypt_credential,
    encrypt_credential,
    mask_credential,
)


def test_round_trip_and_ciphertext_does_not_leak_secret():
    ciphertext, credential_type, has_credential = encrypt_credential(
        CredentialType.BEARER.value, "super-secret", {"X-Tenant": "bjrcb"}
    )
    assert credential_type == "bearer"
    assert has_credential is True
    assert "super-secret" not in ciphertext

    payload = decrypt_credential(ciphertext)
    assert payload.auth_type == "bearer"
    assert payload.secret == "super-secret"
    assert payload.headers == {"X-Tenant": "bjrcb"}


def test_none_credential_is_empty_and_not_has_credential():
    ciphertext, credential_type, has_credential = encrypt_credential(CredentialType.NONE.value)
    assert ciphertext == ""
    assert credential_type == "none"
    assert has_credential is False
    assert decrypt_credential(ciphertext).secret == ""


def test_mask_only_for_configured_credentials():
    assert mask_credential(CredentialType.BEARER.value, True) == MASKED_SECRET
    assert mask_credential(CredentialType.NONE.value, False) is None
    assert mask_credential(CredentialType.BEARER.value, False) is None


@pytest.mark.parametrize(
    "auth_type,secret,headers",
    [
        ("weird", "x", None),
        (CredentialType.BEARER.value, "", None),
        (CredentialType.API_KEY.value, None, None),
        (CredentialType.BEARER.value, "x", {"bad\nkey": "v"}),
        (CredentialType.BEARER.value, "x", {"k": "v\nInjected: 1"}),
    ],
)
def test_invalid_credentials_rejected(auth_type, secret, headers):
    with pytest.raises(ManagedAgentCredentialInvalidError):
        encrypt_credential(auth_type, secret, headers)


def test_tampered_ciphertext_is_normalized_to_safe_error():
    with pytest.raises(ManagedAgentCredentialInvalidError):
        decrypt_credential("not-a-fernet-token")
