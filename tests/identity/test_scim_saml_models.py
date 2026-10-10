"""Refusal tests for the .1 typed models: R011, R012."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.saml import SamlServiceProvider
from graph_os.identity.scim import ScimServiceCredential

_CERT = "-----BEGIN CERTIFICATE-----\nMII...\n-----END CERTIFICATE-----"


def test_scim_credential_authorizes_matching_provider() -> None:
    credential = ScimServiceCredential(provider_id="okta", token_id="t1")
    assert credential.authorizes("okta") is True


def test_scim_credential_refuses_mismatched_provider() -> None:
    credential = ScimServiceCredential(provider_id="okta", token_id="t1")
    assert credential.authorizes("azuread") is False


def test_scim_credential_refuses_empty_provider_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimServiceCredential(provider_id="", token_id="t1")


def test_saml_service_provider_accepts_valid_config() -> None:
    provider = SamlServiceProvider(
        entity_id="urn:graph-os:sp",
        acs_url="https://graph-os.example.org/saml/acs",
        idp_certificate_pem=_CERT,
    )
    assert provider.entity_id == "urn:graph-os:sp"


def test_saml_service_provider_refuses_non_url_acs() -> None:
    with pytest.raises(IdentityUnavailable):
        SamlServiceProvider(
            entity_id="urn:graph-os:sp", acs_url="not-a-url", idp_certificate_pem=_CERT
        )


def test_saml_service_provider_refuses_missing_certificate() -> None:
    with pytest.raises(IdentityUnavailable):
        SamlServiceProvider(
            entity_id="urn:graph-os:sp",
            acs_url="https://graph-os.example.org/saml/acs",
            idp_certificate_pem="not a cert",
        )
