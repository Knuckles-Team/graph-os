"""Claim preparation tests; no signing authority is qualified by these fixtures."""

import asyncio
import json

import pytest

from graph_os.identity.engine import IdentityUnavailable, Resolution
from graph_os.identity.issuer import (
    ISSUER_KEY_RING_PATH,
    IssuerSettings,
    LocalIssuer,
    claims_for,
)
from graph_os.identity.ports import CredentialState

from .test_engine_resolution import eg_public_resolution_fixture, resolution_value

SETTINGS = IssuerSettings(
    "https://issuer.invalid", "fixture-audience", "fixture-tenant"
)


@pytest.mark.parametrize(
    ("source_expiry", "expected"), [(500_000, 400), (150_999, 150)]
)
def test_token_expiry_never_outlives_source(source_expiry, expected):
    claims = claims_for(
        Resolution.parse(resolution_value()),
        SETTINGS,
        source_expires_at_ms=source_expiry,
        now_ms=100_100,
    )
    assert claims["exp"] == expected
    assert claims["exp"] * 1000 <= source_expiry
    assert 0 < claims["exp"] - claims["iat"] <= 300
    assert claims["sub"] == claims["agent_id"] == "usr:fixture"
    assert claims["delegation"] == []
    assert claims["policy_version"] == "fixture-policy"
    assert claims["scope"] == "kg:read"
    assert claims["roles"] == ["reader"]
    assert "realm_access" not in claims


@pytest.mark.parametrize("expiry", [0, 99_000, 100_100, 100_999])
def test_expired_or_subsecond_source_cannot_issue(expiry):
    with pytest.raises(PermissionError):
        claims_for(
            Resolution.parse(resolution_value()),
            SETTINGS,
            source_expires_at_ms=expiry,
            now_ms=100_100,
        )


@pytest.mark.parametrize("expiry", [None, True, "200000", 200000.0, -1])
def test_missing_or_malformed_source_expiry_never_defaults(expiry):
    with pytest.raises(IdentityUnavailable):
        claims_for(
            Resolution.parse(resolution_value()),
            SETTINGS,
            source_expires_at_ms=expiry,
            now_ms=100_100,
        )


@pytest.mark.parametrize("field", ["tenant", "audience"])
def test_deployment_mismatch_refuses(field):
    value = resolution_value()
    value["request_context"][field] = "different"
    with pytest.raises(IdentityUnavailable):
        claims_for(
            Resolution.parse(value),
            SETTINGS,
            source_expires_at_ms=200_000,
            now_ms=100_000,
        )


def test_narrowed_claims_and_context_preserve_exact_empty_scopes():
    resolution = Resolution.parse(resolution_value()).narrow(())
    claims = claims_for(
        resolution, SETTINGS, source_expires_at_ms=200_000, now_ms=100_000
    )
    assert claims["scope"] == ""
    assert resolution.request_context["scopes"] == ()
    assert claims["roles"] == ["reader"]


@pytest.mark.parametrize("ttl", [True, 0, 301, "300", 300.0])
def test_ttl_does_not_coerce(ttl):
    with pytest.raises(ValueError):
        IssuerSettings(
            "https://issuer.invalid", "fixture-audience", "fixture-tenant", ttl
        )


def test_public_eg_context_does_not_supply_missing_credential_expiry():
    resolution = Resolution.from_reply(eg_public_resolution_fixture())
    with pytest.raises(IdentityUnavailable):
        claims_for(
            resolution,
            IssuerSettings("https://issuer.invalid", "engine", "tenant"),
            source_expires_at_ms=None,
            now_ms=100_000,
        )


@pytest.fixture(scope="module")
def synthetic_signing_key():
    """Ephemeral test-only RSA key; never stored in a deployment secret backend."""
    from joserfc.jwk import RSAKey

    return {
        **RSAKey.generate_key(2048, private=True).as_dict(private=True),
        "kid": "fixture-key",
        "use": "sig",
        "alg": "RS256",
    }


class ExistingFixtureKeyStore:
    def __init__(self, active):
        self.value = json.dumps({"active": active}) if active is not None else None
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        return self.value


class CredentialFixture:
    def __init__(self):
        self.state = CredentialState(Resolution.parse(resolution_value()), 150_999)
        self.calls = []
        self.second_error = None
        self.fresh_state = None

    async def resolve_credential(self, credential):
        self.calls.append(credential)
        if len(self.calls) == 2 and self.second_error is not None:
            raise self.second_error
        if len(self.calls) == 2 and self.fresh_state is not None:
            return self.fresh_state
        return self.state


def test_configured_existing_key_signs_exact_source_bounded_profile(
    synthetic_signing_key,
):
    from joserfc import jwt
    from joserfc.jwk import RSAKey

    store, authority = (
        ExistingFixtureKeyStore(synthetic_signing_key),
        CredentialFixture(),
    )
    issuer = LocalIssuer(store, SETTINGS, authority, clock=lambda: 100.1)
    token = asyncio.run(issuer.issue("exact-fixture-credential"))
    decoded = jwt.decode(
        token, RSAKey.import_key(synthetic_signing_key), algorithms=["RS256"]
    )
    assert decoded.claims["exp"] == 150
    assert decoded.claims["sub"] == "usr:fixture"
    assert decoded.claims["policy_version"] == "fixture-policy"
    assert decoded.claims["delegation"] == []
    assert store.reads == [ISSUER_KEY_RING_PATH]
    assert authority.calls == ["exact-fixture-credential", "exact-fixture-credential"]


def test_missing_signing_key_does_not_provision(synthetic_signing_key):
    store = ExistingFixtureKeyStore(None)
    with pytest.raises(IdentityUnavailable):
        asyncio.run(
            LocalIssuer(store, SETTINGS, CredentialFixture(), clock=lambda: 100).issue(
                "fixture"
            )
        )
    assert store.value is None
    assert store.reads == [ISSUER_KEY_RING_PATH]


@pytest.mark.parametrize(
    "error",
    [PermissionError("revoked"), RuntimeError("transport"), asyncio.CancelledError()],
)
def test_recheck_revocation_infrastructure_and_cancellation_propagate(
    synthetic_signing_key, error
):
    authority = CredentialFixture()
    authority.second_error = error
    issuer = LocalIssuer(
        ExistingFixtureKeyStore(synthetic_signing_key),
        SETTINGS,
        authority,
        clock=lambda: 100,
    )
    with pytest.raises(type(error)):
        asyncio.run(issuer.issue("fixture"))


def test_missing_eg_source_expiry_adapter_refuses_before_key_read(
    synthetic_signing_key,
):
    from graph_os.identity.engine_ports import UnavailableIdentityAuthority

    store = ExistingFixtureKeyStore(synthetic_signing_key)
    issuer = LocalIssuer(store, SETTINGS, UnavailableIdentityAuthority())
    with pytest.raises(IdentityUnavailable, match="expiry binding unavailable"):
        asyncio.run(issuer.issue("fixture"))
    assert store.reads == []


@pytest.mark.parametrize("change", ["scope", "policy", "expiry"])
def test_authority_change_during_issuance_refuses_signed_result(
    synthetic_signing_key, change
):
    authority = CredentialFixture()
    resolution, expiry = authority.state.resolution, authority.state.expires_at_ms
    if change == "scope":
        resolution = resolution.narrow(())
    elif change == "policy":
        value = resolution_value()
        value["request_context"]["policy_version"] = "new-policy"
        resolution = Resolution.parse(value)
    else:
        expiry = 120_000
    authority.fresh_state = CredentialState(resolution, expiry)
    issuer = LocalIssuer(
        ExistingFixtureKeyStore(synthetic_signing_key),
        SETTINGS,
        authority,
        clock=lambda: 100,
    )
    with pytest.raises(PermissionError, match="changed during issuance"):
        asyncio.run(issuer.issue("fixture"))


@pytest.mark.parametrize("change", ["algorithm", "public_only", "kid"])
def test_malformed_existing_key_refuses_without_replacement(
    synthetic_signing_key, change
):
    key = dict(synthetic_signing_key)
    if change == "algorithm":
        key["alg"] = "RS512"
    elif change == "public_only":
        del key["d"]
    else:
        key["kid"] = ""
    store = ExistingFixtureKeyStore(key)
    original = store.value
    with pytest.raises(IdentityUnavailable):
        asyncio.run(
            LocalIssuer(store, SETTINGS, CredentialFixture(), clock=lambda: 100).issue(
                "fixture"
            )
        )
    assert store.value == original


@pytest.mark.parametrize("substitute", [False, True])
def test_admission_owns_exact_cookie_token_and_caller_pairing(
    synthetic_signing_key, substitute
):
    from types import SimpleNamespace

    from joserfc import jwt
    from joserfc.jwk import RSAKey

    from graph_os.identity.admission import BrowserAdmission

    from .test_console_admission import CallerFixture, OwnersFixture
    from .test_modes_and_browser import ORIGIN, TOKEN, request_scope

    async def scenario():
        class Backend(OwnersFixture):
            async def resolve_credential(self, credential):
                assert credential == TOKEN
                return self.state

        backend = Backend()
        key = RSAKey.import_key(synthetic_signing_key)

        async def verified(token):
            decoded = jwt.decode(token, key, algorithms=["RS256"])
            registry = jwt.JWTClaimsRegistry(
                now=lambda: backend.now,
                iss={"essential": True, "value": SETTINGS.issuer},
                aud={"essential": True, "value": SETTINGS.audience},
                exp={"essential": True},
            )
            registry.validate(decoded.claims)
            return SimpleNamespace(
                claims=decoded.claims,
                ensure_current=lambda: registry.validate(decoded.claims),
            )

        backend.verify_token = verified
        owner = backend.producer()
        issuer = LocalIssuer(
            ExistingFixtureKeyStore(synthetic_signing_key),
            SETTINGS,
            backend,
            clock=lambda: backend.now,
        )
        sessions = []

        async def session_for_token(token):
            await verified(token)
            session = CallerFixture(backend.resolution)
            sessions.append(session)
            return session

        admission = BrowserAdmission(
            issuer, owner, session_for_token, trusted_origin=ORIGIN
        )
        original = request_scope()
        async with admission.request(original) as forwarded:
            session = await owner.session_for_request(SimpleNamespace(scope=forwarded))
            assert session is sessions[0]
            assert forwarded is not original
            assert forwarded["state"]["graphos_session_admitted"] is True
            if substitute:
                other = await issuer.issue(TOKEN)
                forwarded["headers"] = [
                    (k, v) for k, v in forwarded["headers"] if k != b"authorization"
                ]
                forwarded["headers"].append(
                    (b"authorization", f"Bearer {other}".encode())
                )
                with pytest.raises(PermissionError):
                    await owner.before_invocation(forwarded, session)
            else:
                await owner.before_invocation(forwarded, session)
        assert owner._requests == {}
        assert not any(k == b"authorization" for k, _ in original["headers"])

    asyncio.run(scenario())


def test_admission_rejects_incoming_cookie_bearer_before_issuing(synthetic_signing_key):
    from graph_os.identity.admission import BrowserAdmission

    from .test_console_admission import OwnersFixture, forwarded_scope
    from .test_modes_and_browser import ORIGIN

    async def scenario():
        backend = OwnersFixture()
        store = ExistingFixtureKeyStore(synthetic_signing_key)
        authority = CredentialFixture()
        issuer = LocalIssuer(store, SETTINGS, authority, clock=lambda: 100)

        async def factory(token):
            pytest.fail("ambiguous credentials reached caller conversion")

        admission = BrowserAdmission(
            issuer, backend.producer(), factory, trusted_origin=ORIGIN
        )
        with pytest.raises(PermissionError, match="ambiguous"):
            async with admission.request(forwarded_scope()):
                pytest.fail("ambiguous credentials admitted")
        assert authority.calls == [] and store.reads == []

    asyncio.run(scenario())
