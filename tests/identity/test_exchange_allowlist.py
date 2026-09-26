"""Only a registered upstream provider for this tenant may issue a local token."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anyio
import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.applications import Starlette

from graph_os.identity import exchange
from graph_os.identity.composition import build_identity_runtime
from graph_os.identity.engine import IdentityCall, IdentityReply
from graph_os.identity.exchange import UpstreamAssertion
from graph_os.identity.idp_common import ExternalAssertion, IdpRecord
from graph_os.identity.oidc import OidcBroker, OidcSettings, ProviderMetadata

from .gate_harness import BASE, deployment
from .store_double import SecretsDouble, StoreDouble


class Verifier:
    def __init__(self, assertion: UpstreamAssertion) -> None:
        self.assertion = assertion

    async def verify(self, token: str) -> UpstreamAssertion:
        assert token == "upstream-token"
        return self.assertion


@pytest.mark.parametrize(
    ("idp_id", "tenant_id", "subject"),
    [
        ("unregistered", "tenant-a", "upstream-user"),
        ("registered", "tenant-b", "upstream-user"),
        ("registered", "tenant-a", ""),
    ],
)
def test_upstream_assertion_must_match_registered_provider_and_tenant(
    monkeypatch: pytest.MonkeyPatch,
    idp_id: str,
    tenant_id: str,
    subject: str,
) -> None:
    async def unexpected_exchange(*_args: object) -> str:
        raise AssertionError("untrusted assertion reached external_login")

    monkeypatch.setattr(exchange, "exchange_upstream", unexpected_exchange)
    broker = SimpleNamespace(
        issuer=SimpleNamespace(settings=SimpleNamespace(tenant="tenant-a"))
    )
    assertion = UpstreamAssertion(idp_id, tenant_id, subject)
    route = exchange._Exchange(
        SimpleNamespace(broker=broker), {"registered": Verifier(assertion)}
    )
    assert asyncio.run(route._upstream_token("upstream-token", None)) is None


def test_matching_upstream_assertion_reaches_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[UpstreamAssertion] = []

    async def accept(_broker: object, assertion: UpstreamAssertion, _ip: object) -> str:
        seen.append(assertion)
        return "local-token"

    monkeypatch.setattr(exchange, "exchange_upstream", accept)
    broker = SimpleNamespace(
        issuer=SimpleNamespace(settings=SimpleNamespace(tenant="tenant-a"))
    )
    assertion = UpstreamAssertion("registered", "tenant-a", "upstream-user")
    route = exchange._Exchange(
        SimpleNamespace(broker=broker), {"registered": Verifier(assertion)}
    )
    assert asyncio.run(route._upstream_token("upstream-token", None)) == "local-token"
    assert seen == [assertion]


def test_two_registered_verifiers_cannot_choose_a_principal_by_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_exchange(*_args: object) -> str:
        raise AssertionError("ambiguous upstream identity reached external_login")

    monkeypatch.setattr(exchange, "exchange_upstream", unexpected_exchange)
    broker = SimpleNamespace(
        issuer=SimpleNamespace(settings=SimpleNamespace(tenant="tenant-a"))
    )
    route = exchange._Exchange(
        SimpleNamespace(broker=broker),
        {
            "first": Verifier(UpstreamAssertion("first", "tenant-a", "user-one")),
            "second": Verifier(UpstreamAssertion("second", "tenant-a", "user-two")),
        },
    )
    assert asyncio.run(route._upstream_token("upstream-token", None)) is None


def test_temporary_upstream_session_is_revoked_if_resolution_fails() -> None:
    issued: list[str] = []
    revoked: list[str] = []

    class Engine:
        async def broker(self, call: IdentityCall) -> IdentityReply:
            assert call.request is not None
            session = str(call.request["session_token"])
            issued.append(session)
            return IdentityReply("authenticate", {"outcome": "ok"})

    class Broker:
        engine = Engine()

        async def resolve_session(self, session: str) -> None:
            raise RuntimeError("identity store unavailable")

        async def sign_out(self, session: str) -> None:
            revoked.append(session)

    with pytest.raises(RuntimeError, match="identity store unavailable"):
        asyncio.run(
            exchange.exchange_upstream(
                Broker(), UpstreamAssertion("idp-one", "tenant-a", "user"), None
            )
        )
    assert len(issued) == 1 and revoked == issued


def test_upstream_exchange_revokes_pending_mfa_session() -> None:
    opened: list[str] = []
    revoked: list[str] = []

    class Engine:
        async def broker(self, call: IdentityCall) -> IdentityReply:
            assert call.request is not None
            opened.append(str(call.request["session_token"]))
            return IdentityReply("authenticate", {"outcome": "mfa_required"})

    class Broker:
        engine = Engine()

        async def resolve_session(self, _session: str) -> None:
            raise AssertionError("pending MFA session must not be resolved")

        async def sign_out(self, session: str) -> None:
            revoked.append(session)

    result = asyncio.run(
        exchange.exchange_upstream(
            Broker(), UpstreamAssertion("idp-one", "tenant-a", "user"), None
        )
    )
    assert result is None
    assert len(opened) == 1 and revoked == opened


def test_installed_gate_forwards_registered_upstream_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def accept(
        _broker: object, _assertion: UpstreamAssertion, _ip: object
    ) -> str:
        return "local-token"

    monkeypatch.setattr(exchange, "exchange_upstream", accept)
    runtime = build_identity_runtime(
        deployment(), secrets=SecretsDouble(), engine=StoreDouble()
    )
    app = Starlette()
    runtime.install(
        app,
        lambda _scopes: None,
        upstream={
            "registered": Verifier(UpstreamAssertion("registered", "local", "user"))
        },
    )

    async def request_token() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE
        ) as client:
            return await client.post(
                "/oauth/token",
                data={
                    "grant_type": exchange.TOKEN_EXCHANGE_GRANT,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                    "subject_token": "upstream-token",
                },
            )

    response = asyncio.run(request_token())
    assert response.status_code == 200
    assert response.json()["access_token"] == "local-token"


def test_enabled_oidc_provider_verifies_signed_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def inline(func: Callable[..., Any], *args: object) -> Any:
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", inline)
    now = int(time.time())
    settings = OidcSettings(
        issuer="https://idp.example",
        client_id="graphos-client",
        redirect_uri="https://graphos.example/auth/oidc/callback",
    )
    record = IdpRecord(
        "idp-one", "oidc", "IdP", True, settings.model_dump(), None, (), 0
    )
    key = RSAKey.generate_key(2048, private=True)
    signing_key = RSAKey.import_key({**key.as_dict(private=True), "kid": "kid-1"})
    keys = KeySet.import_key_set(
        {"keys": [{**key.as_dict(private=False), "kid": "kid-1"}]}
    )
    rows = [record]

    class Directory:
        async def records(self) -> tuple[IdpRecord, ...]:
            return tuple(rows)

    class Providers:
        def metadata(self, issuer: str) -> ProviderMetadata:
            assert issuer == settings.issuer
            return ProviderMetadata(
                issuer,
                "https://idp.example/auth",
                "https://idp.example/token",
                "https://idp.example/jwks",
                None,
            )

        def keys(self, _metadata: ProviderMetadata, kid: str | None) -> KeySet:
            assert kid == "kid-1"
            return keys

    broker = object.__new__(OidcBroker)
    broker._directory = Directory()
    broker._providers = Providers()
    broker._clock = lambda: float(now)

    def token(*, typ: str = "at+jwt", audience: str = settings.client_id) -> str:
        return jwt.encode(
            {"alg": "RS256", "typ": typ, "kid": "kid-1"},
            {
                "iss": settings.issuer,
                "aud": audience,
                "sub": "provider-subject",
                "iat": now,
                "exp": now + 300,
                "preferred_username": "alice",
            },
            signing_key,
        )

    assertion = asyncio.run(broker.verify_exchange_access_token(token()))
    assert assertion is not None
    assert assertion.idp_id == "idp-one" and assertion.subject == "provider-subject"
    assert asyncio.run(broker.verify_exchange_access_token(token(typ="JWT"))) is None
    assert (
        asyncio.run(broker.verify_exchange_access_token(token(audience="other")))
        is None
    )
    rows.append(
        IdpRecord("idp-two", "oidc", "Other", True, settings.model_dump(), None, (), 1)
    )
    assert asyncio.run(broker.verify_exchange_access_token(token())) is None


def test_installed_gate_uses_configured_oidc_access_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def verified(token: str) -> ExternalAssertion:
        assert token == "upstream-token"
        return ExternalAssertion("idp-one", "subject", {}, None)

    async def accept(_broker: object, assertion: UpstreamAssertion, _ip: object) -> str:
        assert assertion.idp_id == "idp-one"
        assert assertion.tenant_id == "local"
        return "local-token"

    monkeypatch.setattr(exchange, "exchange_upstream", accept)
    runtime = build_identity_runtime(
        deployment(), secrets=SecretsDouble(), engine=StoreDouble()
    )
    monkeypatch.setattr(runtime.external.oidc, "verify_exchange_access_token", verified)
    app = Starlette()
    runtime.install(app, lambda _scopes: None)

    async def request_token(token_type: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE
        ) as client:
            return await client.post(
                "/oauth/token",
                data={
                    "grant_type": exchange.TOKEN_EXCHANGE_GRANT,
                    "subject_token_type": token_type,
                    "subject_token": "upstream-token",
                },
            )

    access = asyncio.run(request_token("urn:ietf:params:oauth:token-type:access_token"))
    assert access.status_code == 200
    assert access.json()["access_token"] == "local-token"
    id_token = asyncio.run(request_token("urn:ietf:params:oauth:token-type:id_token"))
    assert id_token.status_code == 400
    assert id_token.json() == {"error": "invalid_grant"}
