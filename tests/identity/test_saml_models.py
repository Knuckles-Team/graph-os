"""Refusal tests for the .1 typed SAML service-provider model: R012.1."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.saml import SamlServiceProvider

_CERT = "-----BEGIN CERTIFICATE-----\nMII...\n-----END CERTIFICATE-----"


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.1")
def test_saml_service_provider_accepts_valid_config() -> None:
    provider = SamlServiceProvider(
        entity_id="urn:graph-os:sp",
        acs_url="https://graph-os.example.org/saml/acs",
        idp_certificate_pem=_CERT,
    )
    assert provider.entity_id == "urn:graph-os:sp"


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.1")
def test_saml_service_provider_refuses_non_url_acs() -> None:
    with pytest.raises(IdentityUnavailable):
        SamlServiceProvider(
            entity_id="urn:graph-os:sp", acs_url="not-a-url", idp_certificate_pem=_CERT
        )


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.1")
def test_saml_service_provider_refuses_missing_certificate() -> None:
    with pytest.raises(IdentityUnavailable):
        SamlServiceProvider(
            entity_id="urn:graph-os:sp",
            acs_url="https://graph-os.example.org/saml/acs",
            idp_certificate_pem="not a cert",
        )
