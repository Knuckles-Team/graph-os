"""Bound refusal tests for the typed SCIM service credential (R011.1)."""

import dataclasses

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.scim import ScimServiceCredential


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.1")
def test_scim_credential_refuses_empty_provider_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimServiceCredential(provider_id="", token_id="t1")


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.1")
def test_scim_credential_refuses_empty_token_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimServiceCredential(provider_id="okta", token_id="")


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.1")
def test_scim_credential_authorizes_only_its_own_provider() -> None:
    credential = ScimServiceCredential(provider_id="okta", token_id="t1")
    assert credential.authorizes("okta") is True
    assert credential.authorizes("azuread") is False
    assert credential.authorizes("") is False


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.1")
def test_scim_credential_is_immutable() -> None:
    credential = ScimServiceCredential(provider_id="okta", token_id="t1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        credential.provider_id = "azuread"
