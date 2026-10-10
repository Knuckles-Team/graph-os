"""Spec-bound refusal tests for the typed OIDC mapping-rule model."""

import dataclasses
from typing import Any

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.oidc import OidcMappingRule


def _rule(**overrides: Any) -> OidcMappingRule:
    fields: dict[str, Any] = {
        "provider_id": "keycloak",
        "order": 3,
        "claim_match": "role=operator",
        "jit_policy": "deny",
    }
    fields.update(overrides)
    return OidcMappingRule(**fields)


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.1")
@pytest.mark.parametrize("policy", ["create", "deny"])
def test_rule_accepts_each_known_jit_policy(policy: str) -> None:
    assert _rule(jit_policy=policy).jit_policy == policy


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.1")
@pytest.mark.parametrize("policy", ["ignore", "", "CREATE"])
def test_rule_refuses_unknown_jit_policy(policy: str) -> None:
    with pytest.raises(IdentityUnavailable):
        _rule(jit_policy=policy)


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.1")
@pytest.mark.parametrize("order", [-1, -100, True, 1.5])
def test_rule_refuses_negative_or_non_integer_order(order: Any) -> None:
    with pytest.raises(IdentityUnavailable):
        _rule(order=order)


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.1")
def test_rule_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _rule().order = 9
