"""Refusal tests for the .1 typed models: R008, R009, R010."""

import pytest

from graph_os.identity.api_keys import ApiKeyGrant
from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.ldap import LdapBindConfig
from graph_os.identity.oidc import OidcMappingRule


def test_api_key_accepts_non_approver_scopes() -> None:
    grant = ApiKeyGrant(
        key_id="k1", owner_principal_id="p1", scopes=frozenset({"domain:read"})
    )
    assert "domain:read" in grant.scopes


def test_api_key_refuses_approver_scope() -> None:
    with pytest.raises(IdentityUnavailable):
        ApiKeyGrant(
            key_id="k1", owner_principal_id="p1", scopes=frozenset({"approver"})
        )


def test_api_key_refuses_empty_owner() -> None:
    with pytest.raises(IdentityUnavailable):
        ApiKeyGrant(key_id="k1", owner_principal_id="", scopes=frozenset())


def test_oidc_mapping_rule_accepts_known_policy() -> None:
    rule = OidcMappingRule(
        provider_id="okta", order=0, claim_match="group=admins", jit_policy="create"
    )
    assert rule.jit_policy == "create"


def test_oidc_mapping_rule_refuses_unknown_policy() -> None:
    with pytest.raises(IdentityUnavailable):
        OidcMappingRule(
            provider_id="okta", order=0, claim_match="group=admins", jit_policy="ignore"
        )


def test_oidc_mapping_rule_refuses_negative_order() -> None:
    with pytest.raises(IdentityUnavailable):
        OidcMappingRule(
            provider_id="okta", order=-1, claim_match="group=admins", jit_policy="deny"
        )


def test_ldap_bind_accepts_ldaps() -> None:
    config = LdapBindConfig(
        host="dc.example.org", port=636, bind_dn="cn=svc,dc=example,dc=org"
    )
    assert config.scheme == "ldaps"


def test_ldap_bind_refuses_plaintext_scheme() -> None:
    with pytest.raises(IdentityUnavailable):
        LdapBindConfig(
            host="dc.example.org",
            port=389,
            bind_dn="cn=svc,dc=example,dc=org",
            scheme="ldap",
        )


def test_ldap_bind_refuses_invalid_port() -> None:
    with pytest.raises(IdentityUnavailable):
        LdapBindConfig(
            host="dc.example.org", port=0, bind_dn="cn=svc,dc=example,dc=org"
        )
