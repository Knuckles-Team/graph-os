"""OIDC relying party: the served routes against a mock IdP, both ways.

Every refusal case pairs with the accepted baseline built by the same helper,
so a check that silently passes everything fails the baseline's twin.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.identity.idp_common import (
    SESSION_COOKIE,
    EngineLoginCompleter,
    IdpDirectory,
    OneShotStore,
)
from graph_os.identity.oidc import (
    IDT_COOKIE,
    TX_COOKIE,
    OidcBroker,
    OidcSettings,
    ProviderCache,
)
from tests.identity.fakes import FakeIdentityPort, FakeSecrets, idp_wire

ISSUER = "https://kc.example/realms/homelab"
CLIENT = "graph-os"
REDIRECT = "https://graphos.example/auth/oidc/callback"


class MockIdp:
    """Discovery, JWKS and a token endpoint that returns ``next_id_token``."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.jwks_keys = [self.key]
        self.next_id_token = ""
        self.token_forms: list[dict[str, list[str]]] = []
        self.jwks_fetches = 0
        self.discovery: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
            "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
            "code_challenge_methods_supported": ["plain", "S256"],
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=self.discovery)
        if path.endswith("/certs"):
            self.jwks_fetches += 1
            keys = [k.as_dict(private=False) | {"use": "sig"} for k in self.jwks_keys]
            return httpx.Response(200, json={"keys": keys})
        if path.endswith("/token"):
            self.token_forms.append(parse_qs(request.content.decode()))
            return httpx.Response(
                200, json={"access_token": "at", "id_token": self.next_id_token}
            )
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def sign(self, claims: dict[str, Any], *, key: Any = None, alg: str = "RS256") -> str:
        return jwt.encode({"alg": alg, "kid": "k1"}, claims, key or self.key)


class ManualClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _claims(nonce: str, /, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "3f1c-kc-uuid",
        "exp": now + 300,
        "iat": now,
        "nonce": nonce,
        "preferred_username": "alice",
        "email": "alice@example.org",
        "email_verified": True,
        "groups": ["/elevation-approvers", "/agent-webui-users"],
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def world() -> Any:
    idp = MockIdp()
    clock = ManualClock()
    config = OidcSettings(
        issuer=ISSUER,
        client_id=CLIENT,
        redirect_uri=REDIRECT,
        post_logout_redirect_uri="https://graphos.example/",
    ).model_dump()
    port = FakeIdentityPort([idp_wire("keycloak", "oidc", config, secret_ref="kc/secret")])
    secrets = FakeSecrets({"kc/secret": "s3cret"})
    broker = OidcBroker(
        port=port,
        directory=IdpDirectory(port),
        transactions=OneShotStore(secrets, "oidc-tx"),
        completer=EngineLoginCompleter(port),
        secrets=secrets,
        providers=ProviderCache(idp.client, clock=clock),
    )
    app = Starlette(routes=broker.routes())
    client = TestClient(app, base_url="https://graphos.example", follow_redirects=False)

    return SimpleNamespace(idp=idp, port=port, client=client, secrets=secrets, clock=clock)


def _begin(world: Any) -> tuple[str, str]:
    response = world.client.get("/auth/oidc/keycloak/login")
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [REDIRECT]
    return query["state"][0], query["nonce"][0]


def _callback(world: Any, state: str) -> httpx.Response:
    return world.client.get("/auth/oidc/callback", params={"code": "c0de", "state": state})


def test_valid_id_token_signs_in_through_the_engine(world: Any) -> None:
    state, nonce = _begin(world)
    world.idp.next_id_token = world.idp.sign(_claims(nonce))

    response = _callback(world, state)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in response.cookies
    (login,) = world.port.ops("credential", "external_login")
    request = login["request"]
    assert request["idp_id"] == "keycloak"
    assert request["subject"] == "3f1c-kc-uuid"
    assert request["claims"]["groups"] == ["elevation-approvers", "agent-webui-users"]
    assert request["claims"]["email_verified"] == ["true"]
    assert request["username_hint"] == "alice"
    assert request["session_token"] == response.cookies[SESSION_COOKIE]
    form = world.idp.token_forms[0]
    assert form["client_secret"] == ["s3cret"]
    assert "code_verifier" in form
    assert IDT_COOKIE in response.headers.get("set-cookie", "")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hs256_with_public_key(world: Any, nonce: str) -> str:
    """The classic algorithm-confusion forgery: HMAC keyed by the public PEM."""
    public_pem = world.idp.key.as_pem(private=False)
    signing_input = ".".join(
        _b64(json.dumps(part).encode())
        for part in ({"alg": "HS256", "kid": "k1"}, _claims(nonce))
    )
    mac = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(mac)}"


def _alg_none(world: Any, nonce: str) -> str:
    header = _b64(json.dumps({"alg": "none", "kid": "k1"}).encode())
    return f"{header}.{_b64(json.dumps(_claims(nonce)).encode())}."


def _tampered(world: Any, nonce: str) -> str:
    header, _, signature = world.idp.sign(_claims(nonce)).split(".")
    forged = _claims(nonce, sub="usr:bootstrap")
    return f"{header}.{_b64(json.dumps(forged).encode())}.{signature}"


def _foreign_key(world: Any, nonce: str) -> str:
    attacker = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    return world.idp.sign(_claims(nonce), key=attacker)


REFUSED = {
    "wrong issuer": lambda w, n: w.idp.sign(_claims(n, iss="https://evil.example/realms/x")),
    "wrong audience": lambda w, n: w.idp.sign(_claims(n, aud="another-client")),
    "multi-aud without azp": lambda w, n: w.idp.sign(_claims(n, aud=[CLIENT, "other"])),
    "azp is another client": lambda w, n: w.idp.sign(_claims(n, azp="other")),
    "expired": lambda w, n: w.idp.sign(_claims(n, exp=int(time.time()) - 3600)),
    "not yet valid": lambda w, n: w.idp.sign(_claims(n, nbf=int(time.time()) + 3600)),
    "nonce mismatch": lambda w, n: w.idp.sign(_claims("another-nonce")),
    "nonce missing": lambda w, n: w.idp.sign(_claims(n, nonce=None)),
    "sub missing": lambda w, n: w.idp.sign(_claims(n, sub=None)),
    "alg none": _alg_none,
    "HS256 keyed with the RSA public key": _hs256_with_public_key,
    "signature by a key the IdP never published": _foreign_key,
    "payload tampered after signing": _tampered,
    "JWE-shaped token": lambda w, n: "a.b.c.d.e",
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_bad_id_tokens_are_refused_before_the_engine(world: Any, case: str) -> None:
    state, nonce = _begin(world)
    world.idp.next_id_token = REFUSED[case](world, nonce)

    response = _callback(world, state)

    assert response.headers["location"] == "/auth/login?error=idp_unverified"
    assert SESSION_COOKIE not in response.cookies
    assert world.port.ops("credential", "external_login") == []


def test_multi_audience_with_matching_azp_is_accepted(world: Any) -> None:
    state, nonce = _begin(world)
    world.idp.next_id_token = world.idp.sign(_claims(nonce, aud=[CLIENT, "x"], azp=CLIENT))

    assert _callback(world, state).headers["location"] == "/"


def test_state_is_single_use_and_bound_to_the_browser(world: Any) -> None:
    state, nonce = _begin(world)
    world.idp.next_id_token = world.idp.sign(_claims(nonce))
    assert _callback(world, state).headers["location"] == "/"

    world.client.cookies.set(TX_COOKIE, state, domain="graphos.example", path="/auth/oidc/callback")
    replay = _callback(world, state)

    assert replay.headers["location"] == "/auth/login?error=stale_login"
    assert len(world.port.ops("credential", "external_login")) == 1


def test_callback_from_another_browser_is_refused(world: Any) -> None:
    state, nonce = _begin(world)
    world.idp.next_id_token = world.idp.sign(_claims(nonce))
    world.client.cookies.clear()

    response = _callback(world, state)

    assert response.headers["location"] == "/auth/login?error=stale_login"
    assert world.idp.token_forms == []


def test_issuer_mismatch_in_discovery_fails_closed(world: Any) -> None:
    world.idp.discovery["issuer"] = "https://kc.example/realms/other"

    response = world.client.get("/auth/oidc/keycloak/login")

    assert response.headers["location"] == "/auth/login?error=idp_unavailable"


def test_disabled_idp_cannot_begin(world: Any) -> None:
    world.port.idps[0]["enabled"] = False

    response = world.client.get("/auth/oidc/keycloak/login")

    assert response.headers["location"] == "/auth/login?error=idp_unavailable"


def test_unknown_kid_refetches_the_jwks_for_key_rotation(world: Any) -> None:
    state, nonce = _begin(world)
    world.idp.next_id_token = world.idp.sign(_claims(nonce))
    _callback(world, state)
    fetched = world.idp.jwks_fetches

    rotated = RSAKey.generate_key(2048, parameters={"kid": "k2"})
    world.idp.jwks_keys = [world.idp.key, rotated]
    state, nonce = _begin(world)
    world.idp.next_id_token = jwt.encode({"alg": "RS256", "kid": "k2"}, _claims(nonce), rotated)
    # Inside the refresh floor the cached set is kept: the rotated key is not
    # yet known, so the token is refused rather than fetched on demand.
    assert _callback(world, state).headers["location"] == "/auth/login?error=idp_unverified"
    assert world.idp.jwks_fetches == fetched

    world.clock.now += 61
    state, nonce = _begin(world)
    world.idp.next_id_token = jwt.encode({"alg": "RS256", "kid": "k2"}, _claims(nonce), rotated)
    assert _callback(world, state).headers["location"] == "/"
    assert world.idp.jwks_fetches == fetched + 1


def test_logout_revokes_the_session_and_sends_id_token_hint(world: Any) -> None:
    state, nonce = _begin(world)
    id_token = world.idp.sign(_claims(nonce))
    world.idp.next_id_token = id_token
    session = _callback(world, state).cookies[SESSION_COOKIE]

    response = world.client.post("/auth/oidc/logout")

    (revoke,) = world.port.ops("session", "revoke")
    assert revoke["request"] == {"session_token": session}
    target = urlsplit(response.headers["location"])
    query = parse_qs(target.query)
    assert target.path.endswith("/protocol/openid-connect/logout")
    assert query["id_token_hint"] == [id_token]
    assert query["post_logout_redirect_uri"] == ["https://graphos.example/"]


def test_engine_refusal_outcomes_never_set_a_session(world: Any) -> None:
    world.port.handlers[("credential", "external_login")] = lambda op: {
        "kind": "authenticate",
        "value": {"outcome": "throttled", "retry_after_ms": 1000},
    }
    state, nonce = _begin(world)
    world.idp.next_id_token = world.idp.sign(_claims(nonce))

    response = _callback(world, state)

    assert response.headers["location"] == "/auth/login?error=throttled"
    assert SESSION_COOKIE not in response.cookies
    assert IDT_COOKIE not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "field,value",
    [
        ("signing_algs", ("HS256",)),
        ("signing_algs", ("none",)),
        ("issuer", "http://kc.example/realms/homelab"),
        ("scopes", ("profile",)),
    ],
)
def test_unsafe_settings_are_refused(field: str, value: Any) -> None:
    raw = {"issuer": ISSUER, "client_id": CLIENT, "redirect_uri": REDIRECT, field: value}
    with pytest.raises(ValueError):
        OidcSettings.model_validate(raw)
