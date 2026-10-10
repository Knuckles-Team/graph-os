"""Refusal tests for the typed API-key grant model (GRAPHOS-IDENTITY-R008.1)."""

import pytest

from graph_os.identity.api_keys import ApiKeyGrant
from graph_os.identity.engine import IdentityUnavailable


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.1")
@pytest.mark.parametrize("scope", ["approver", "approvals:decide"])
def test_api_key_refuses_every_approver_class_scope(scope: str) -> None:
    with pytest.raises(IdentityUnavailable):
        ApiKeyGrant(
            key_id="k1",
            owner_principal_id="p1",
            scopes=frozenset({"domain:read", scope}),
        )


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.1")
def test_api_key_refuses_empty_owner_principal_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ApiKeyGrant(key_id="k1", owner_principal_id="", scopes=frozenset({"x:read"}))


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.1")
def test_api_key_refuses_empty_key_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ApiKeyGrant(key_id="", owner_principal_id="p1", scopes=frozenset())
