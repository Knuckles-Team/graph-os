"""The persistent local issuer (IDM-06): one ring, sub == principal, rotation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from joserfc.errors import ExpiredTokenError, JoseError

from graph_os.identity.engine import Resolution
from graph_os.identity.issuer import (
    ISSUER_KEYS_SECRET,
    IssuerSettings,
    LocalIssuer,
    Retirement,
    TokenGrant,
)

from .store_double import SecretsDouble

SETTINGS = IssuerSettings(
    issuer="https://graph-os.test", audience="eg", tenant="homelab"
)
ALICE = Resolution(
    principal_id="usr:alice",
    username="alice",
    kind="human",
    status="active",
    roles=frozenset({"reports-reader", "graph-writer"}),
    scopes=frozenset({"kg:read", "kg:write", "identity:self"}),
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def issuer(clock: Clock) -> LocalIssuer:
    return LocalIssuer(SecretsDouble(), SETTINGS, clock=clock)


def _grant(clock: Clock, scopes: frozenset[str] | None = None) -> TokenGrant:
    return TokenGrant(("pwd",), int(clock.now), scopes)


def test_ring_is_created_once_and_persisted(issuer: LocalIssuer) -> None:
    first = issuer.ring().kid
    assert issuer.ring().kid == first
    other_replica = LocalIssuer(issuer._store, SETTINGS)
    assert other_replica.ring().kid == first


def test_token_subject_and_verified_roles_are_distinct_from_scopes(
    issuer: LocalIssuer, clock: Clock
) -> None:
    claims = issuer.verify(issuer.mint(ALICE, _grant(clock)))
    assert claims["sub"] == "usr:alice"
    assert claims["iss"] == SETTINGS.issuer and claims["aud"] == SETTINGS.audience
    assert claims["tenant_id"] == "homelab"
    assert set(claims["scope"].split()) == set(ALICE.scopes)
    assert set(claims["roles"]) == set(ALICE.roles)
    assert set(claims["realm_access"]["roles"]) == set(ALICE.roles)
    assert not set(claims["roles"]) & set(ALICE.scopes)
    assert claims["amr"] == ["pwd"]
    assert claims["exp"] - claims["iat"] == 300


def test_narrowed_grant_intersects_never_widens(
    issuer: LocalIssuer, clock: Clock
) -> None:
    grant = _grant(clock, frozenset({"kg:read", "kg:admin"}))
    claims = issuer.verify(issuer.mint(ALICE, grant))
    assert claims["scope"] == "kg:read"
    assert set(claims["roles"]) == set(ALICE.roles)
    assert set(claims["realm_access"]["roles"]) == set(ALICE.roles)


@pytest.mark.parametrize(
    "resolution",
    [
        Resolution("usr:x", "x", "human", "disabled"),
        Resolution("usr:x", "x", "human", "active", session_mfa_pending=True),
    ],
)
def test_unusable_principal_gets_no_token(
    issuer: LocalIssuer, clock: Clock, resolution: Resolution
) -> None:
    with pytest.raises(PermissionError):
        issuer.mint(resolution, _grant(clock))


def test_overlap_rotation_keeps_old_tokens_valid_for_one_lifetime(
    issuer: LocalIssuer, clock: Clock
) -> None:
    old_token = issuer.mint(ALICE, _grant(clock))
    old_kid = issuer.ring().kid
    new_kid = issuer.rotate(Retirement.OVERLAP)
    assert new_kid != old_kid
    kids = {key["kid"] for key in issuer.jwks()["keys"]}
    assert kids == {old_kid, new_kid}
    assert issuer.verify(old_token)["sub"] == "usr:alice"
    clock.now += SETTINGS.access_ttl_seconds + 1
    assert {key["kid"] for key in issuer.jwks()["keys"]} == {new_kid}


def test_revoking_rotation_kills_earlier_tokens_at_once(
    issuer: LocalIssuer, clock: Clock
) -> None:
    old_token = issuer.mint(ALICE, _grant(clock))
    issuer.rotate(Retirement.REVOKE)
    assert len(issuer.jwks()["keys"]) == 1
    with pytest.raises(JoseError):
        issuer.verify(old_token)


def test_expired_token_is_refused(issuer: LocalIssuer, clock: Clock) -> None:
    token = issuer.mint(ALICE, _grant(clock))
    clock.now += 400
    with pytest.raises(ExpiredTokenError):
        issuer.verify(token)


class RacingSecrets(SecretsDouble):
    """A backend where another replica rotates just before our first CAS."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self._clock = clock
        self.raced = False

    def compare_and_set(
        self, key: str, expected: str, value: str, **metadata: object
    ) -> bool:
        if not self.raced:
            self.raced = True
            rival = LocalIssuer(_Plain(self), SETTINGS, clock=self._clock)
            rival.rotate()
        return super().compare_and_set(key, expected, value)


class _Plain(SecretsDouble):
    """A view of another double's values without its racing behaviour."""

    def __init__(self, backing: SecretsDouble) -> None:
        self.values = backing.values


def test_rotation_converges_under_a_concurrent_writer(clock: Clock) -> None:
    store = RacingSecrets(clock)
    issuer = LocalIssuer(store, SETTINGS, clock=clock)
    first = issuer.ring().kid
    kid = issuer.rotate()
    assert store.raced
    assert issuer.ring().kid == kid != first
    assert ISSUER_KEYS_SECRET in store.values


def test_published_keys_are_public_only(issuer: LocalIssuer) -> None:
    for key in issuer.jwks()["keys"]:
        assert "d" not in key and "p" not in key and key["alg"] == "RS256"


def test_discovery_points_at_the_jwks(issuer: LocalIssuer) -> None:
    document = issuer.discovery()
    assert document["jwks_uri"] == "https://graph-os.test/.well-known/jwks.json"
    assert document["issuer"] == SETTINGS.issuer


def test_process_key_bootstraps_without_a_secrets_backend(clock: Clock) -> None:
    class UnavailableSecrets:
        def get(self, key: str) -> str:
            raise AssertionError("bootstrap must not read the secrets backend")

    first = LocalIssuer(
        UnavailableSecrets(), SETTINGS, clock=clock, process_key_enabled=True
    )
    second = LocalIssuer(
        UnavailableSecrets(), SETTINGS, clock=clock, process_key_enabled=True
    )
    service = Resolution(
        "svc:graph-os",
        "graph-os",
        "service",
        "active",
        scopes=frozenset({"identity:authenticate"}),
    )
    token = first.mint_process(service, TokenGrant(("process",), int(clock.now)))
    assert second.verify_process(token)["sub"] == "svc:graph-os"
    assert (
        second.process_jwks()["keys"][0]["kid"]
        == first.process_jwks()["keys"][0]["kid"]
    )
    assert "d" not in first.process_jwks()["keys"][0]
    with pytest.raises(PermissionError):
        first.mint_process(ALICE, _grant(clock))


def test_process_key_is_published_only_for_tiny_profile(clock: Clock) -> None:
    store = SecretsDouble()
    tiny = LocalIssuer(store, SETTINGS, clock=clock, process_key_enabled=True)
    standard = LocalIssuer(store, SETTINGS, clock=clock)
    persistent_kid = tiny.ring().kid
    assert {key["kid"] for key in tiny.jwks()["keys"]} == {
        persistent_kid,
        tiny.process_jwks()["keys"][0]["kid"],
    }
    assert {key["kid"] for key in standard.jwks()["keys"]} == {persistent_kid}
    with pytest.raises(PermissionError):
        standard.process_jwks()


def test_tiny_broker_session_uses_process_key_before_ring_exists() -> None:
    from graph_os.identity.composition import self_minted_broker_session

    class UnavailableSecrets:
        def get(self, key: str) -> str:
            raise AssertionError("process session must not read the ring")

    issuer = LocalIssuer(UnavailableSecrets(), SETTINGS, process_key_enabled=True)
    broker = SimpleNamespace(issuer=issuer)
    session = self_minted_broker_session(lambda: broker, process_key=True)()
    assert session.actor.actor_id == "svc:graph-os"
    assert "identity:authenticate" in session.actor.roles


@pytest.mark.parametrize("ttl", [0, 301])
def test_settings_refuse_long_or_zero_lifetimes(ttl: int) -> None:
    with pytest.raises(ValueError):
        IssuerSettings(issuer="i", audience="a", tenant="t", access_ttl_seconds=ttl)
