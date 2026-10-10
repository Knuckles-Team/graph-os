"""Refusal tests for the .1 typed model: R011."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.scim import ScimServiceCredential


def test_scim_credential_authorizes_matching_provider() -> None:
    credential = ScimServiceCredential(provider_id="okta", token_id="t1")
    assert credential.authorizes("okta") is True


def test_scim_credential_refuses_mismatched_provider() -> None:
    credential = ScimServiceCredential(provider_id="okta", token_id="t1")
    assert credential.authorizes("azuread") is False


def test_scim_credential_refuses_empty_provider_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimServiceCredential(provider_id="", token_id="t1")
