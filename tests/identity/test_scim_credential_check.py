"""Bound tests for the SCIM request credential check (R011.2.2)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.scim import (
    ScimCredentialRefused,
    ScimServiceCredential,
    check_scim_credential,
)

_ID = "GRAPHOS-IDENTITY-R011.2.2"
_CRED = ScimServiceCredential(provider_id="okta", token_id="t1")
_CREDS = {"secret": _CRED}


@pytest.mark.spec(_ID)
def test_matching_credential_is_returned() -> None:
    assert check_scim_credential(_CREDS, "secret", "okta") is _CRED


@pytest.mark.spec(_ID)
@pytest.mark.parametrize("token", [None, ""])
def test_missing_credential_refused(token: str | None) -> None:
    with pytest.raises(ScimCredentialRefused):
        check_scim_credential(_CREDS, token, "okta")


@pytest.mark.spec(_ID)
def test_unknown_credential_refused() -> None:
    with pytest.raises(ScimCredentialRefused):
        check_scim_credential(_CREDS, "nope", "okta")


@pytest.mark.spec(_ID)
def test_wrong_provider_credential_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        check_scim_credential(_CREDS, "secret", "entra")
