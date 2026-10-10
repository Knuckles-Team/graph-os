"""Contract tests for API-key use-time scope intersection and revocation (GRAPHOS-IDENTITY-R008.2)."""

from datetime import UTC, datetime, timedelta

import pytest

from graph_os.identity.api_keys import ApiKeyGrant, effective_scopes
from graph_os.identity.engine import IdentityUnavailable

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _grant(expires_at: datetime | None = None) -> ApiKeyGrant:
    return ApiKeyGrant(
        key_id="k1",
        owner_principal_id="p1",
        scopes=frozenset({"a:read", "b:write"}),
        expires_at=expires_at,
    )


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.2")
def test_scopes_are_intersected_with_current_owner_scopes() -> None:
    grant = _grant()
    assert effective_scopes(grant, frozenset({"a:read", "c:read"}), now=NOW) == {
        "a:read"
    }
    assert effective_scopes(grant, frozenset(), now=NOW) == frozenset()


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.2")
def test_revoked_key_is_denied_immediately() -> None:
    with pytest.raises(IdentityUnavailable):
        effective_scopes(
            _grant(), frozenset({"a:read"}), now=NOW, revoked_key_ids={"k1"}
        )


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.2")
def test_expired_key_is_denied_and_unexpired_allowed() -> None:
    owner = frozenset({"a:read"})
    with pytest.raises(IdentityUnavailable):
        effective_scopes(_grant(NOW - timedelta(seconds=1)), owner, now=NOW)
    with pytest.raises(IdentityUnavailable):
        effective_scopes(_grant(NOW), owner, now=NOW)
    assert effective_scopes(_grant(NOW + timedelta(hours=1)), owner, now=NOW) == owner


@pytest.mark.spec("GRAPHOS-IDENTITY-R008.2")
def test_approver_scope_never_effective_even_if_owner_holds_it() -> None:
    grant = _grant()
    field = "scopes"
    object.__setattr__(grant, field, frozenset({"a:read", "approver"}))
    out = effective_scopes(grant, frozenset({"a:read", "approver"}), now=NOW)
    assert out == {"a:read"}
