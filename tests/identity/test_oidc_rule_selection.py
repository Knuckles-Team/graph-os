"""Spec-bound tests for ordered OIDC claim-to-rule evaluation."""

from typing import Any

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.oidc import OidcMappingRule, select_mapping_rule


def _rule(order: int, match: str, provider: str = "kc") -> OidcMappingRule:
    return OidcMappingRule(
        provider_id=provider, order=order, claim_match=match, jit_policy="create"
    )


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.1")
def test_lowest_order_matching_rule_wins() -> None:
    rules = [_rule(2, "role=admin"), _rule(1, "role=admin"), _rule(0, "role=ops")]
    got = select_mapping_rule(rules, "kc", {"role": "admin"})
    assert got.order == 1


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.1")
def test_list_claim_matches_member() -> None:
    got = select_mapping_rule([_rule(0, "groups=ops")], "kc", {"groups": ["a", "ops"]})
    assert got.claim_match == "groups=ops"


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.1")
@pytest.mark.parametrize("claims", [{}, {"role": "viewer"}, {"role": 7}])
def test_no_match_is_refused(claims: dict[str, Any]) -> None:
    with pytest.raises(IdentityUnavailable):
        select_mapping_rule([_rule(0, "role=admin")], "kc", claims)


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.1")
def test_other_providers_rules_are_ignored() -> None:
    with pytest.raises(IdentityUnavailable):
        select_mapping_rule([_rule(0, "role=admin", "other")], "kc", {"role": "admin"})


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.1")
def test_duplicate_order_is_refused() -> None:
    rules = [_rule(1, "role=a"), _rule(1, "role=b")]
    with pytest.raises(IdentityUnavailable):
        select_mapping_rule(rules, "kc", {"role": "a"})


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.1")
@pytest.mark.parametrize("match", ["role", "=x", "role="])
def test_malformed_claim_match_is_refused(match: str) -> None:
    with pytest.raises(IdentityUnavailable):
        select_mapping_rule([_rule(0, match)], "kc", {"role": "x"})
