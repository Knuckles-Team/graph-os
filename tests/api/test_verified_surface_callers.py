"""Surface authority regressions using explicit synthetic caller fixtures."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from graph_os.api.http.auth import AmbientHTTPAuthenticator

NOW_MS = 2_000_000
ORIGIN = "https://console.example.test"


def console_request(*, admitted=True, cookie=True, origin=ORIGIN):
    headers = [(b"origin", origin.encode())]
    if cookie:
        headers.append((b"cookie", b"__Host-graphos_session=synthetic-fixture"))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ops/fixture",
            "headers": headers,
            "state": {"graphos_session_admitted": admitted},
        }
    )


@pytest.fixture
def attended_caller():
    return SimpleNamespace(
        credential_kind="session",
        principal_kind="human",
        delegated=False,
        mfa_at_ms=NOW_MS,
    )


@pytest.mark.parametrize(
    ("age_ms", "eligible"),
    [(0, True), (900_000, True), (900_001, False), (-1, False)],
)
def test_console_stepup_exact_freshness_boundary(
    monkeypatch, attended_caller, age_ms, eligible
):
    monkeypatch.setattr("graph_os.api.http.auth.time.time", lambda: NOW_MS / 1000)
    attended_caller.mfa_at_ms = NOW_MS - age_ms
    auth = AmbientHTTPAuthenticator(console_origin=ORIGIN)
    assert auth.is_console_request(console_request(), attended_caller) is eligible


@pytest.mark.parametrize(
    "request_facts",
    [
        {"admitted": False},
        {"admitted": "true"},
        {"admitted": 1},
        {"cookie": False},
        {"origin": "https://console.example.test.attacker.invalid"},
        {"origin": "https://console.example.test/"},
        {"origin": "null"},
    ],
)
def test_console_needs_server_admission_cookie_and_exact_origin(
    monkeypatch, attended_caller, request_facts
):
    monkeypatch.setattr("graph_os.api.http.auth.time.time", lambda: NOW_MS / 1000)
    auth = AmbientHTTPAuthenticator(console_origin=ORIGIN)
    assert not auth.is_console_request(
        console_request(**request_facts), attended_caller
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("credential_kind", "bearer"),
        ("principal_kind", "service"),
        ("principal_kind", "unknown"),
        ("delegated", True),
        ("mfa_at_ms", None),
    ],
)
def test_console_rejects_unattended_authority(
    monkeypatch, attended_caller, field, value
):
    monkeypatch.setattr("graph_os.api.http.auth.time.time", lambda: NOW_MS / 1000)
    setattr(attended_caller, field, value)
    auth = AmbientHTTPAuthenticator(console_origin=ORIGIN)
    assert not auth.is_console_request(console_request(), attended_caller)


def test_console_origin_must_be_configured(monkeypatch, attended_caller):
    monkeypatch.setattr("graph_os.api.http.auth.time.time", lambda: NOW_MS / 1000)
    assert not AmbientHTTPAuthenticator().is_console_request(
        console_request(), attended_caller
    )


class SessionFixture:
    """Synthetic authority fixture, never a production identity minting path."""

    def __init__(self):
        self.actor = SimpleNamespace(
            actor_id="fixture-human",
            actor_type="human",
            tenant_id="fixture-tenant",
            authenticated=True,
            ensure_credential_current=self.ensure_authority_current,
        )
        self.tenant = "fixture-tenant"
        self.policy_version = "fixture-policy-v1"
        self.scopes = frozenset({"fixture:read"})
        self.claims = {
            "principal": self.actor.actor_id,
            "tenant": self.tenant,
            "scopes": ["fixture:read"],
            "policy_version": self.policy_version,
            "delegation": [],
        }
        self.revoked = False

    def ensure_authority_current(self):
        if self.revoked:
            raise PermissionError("fixture revoked")

    def engine_verified_context(self):
        self.ensure_authority_current()
        return self.claims


def bearer_request(*headers):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ops/fixture",
            "headers": list(headers),
            "state": {},
        }
    )


def adapters(session):
    from graph_os.api.mcp.caller import VerifiedMCPCaller

    async def verified_session(_request):
        return session

    return (
        AmbientHTTPAuthenticator(session_for_request=verified_session),
        VerifiedMCPCaller(
            session_for_request=lambda: session,
            credential_kind_for_request=lambda: "bearer",
        ),
    )


def test_http_mcp_use_same_b_owned_caller_and_exact_authority():
    import asyncio

    from graph_os.api.invoke import VerifiedCaller

    session = SessionFixture()
    http, mcp = adapters(session)
    caller = asyncio.run(
        http.authenticate(bearer_request((b"authorization", b"Bearer fixture")))
    )
    assert isinstance(caller, VerifiedCaller)
    assert caller == mcp.caller_for_request()
    assert caller.effective_scopes == frozenset({"fixture:read"})
    assert caller.credential_kind == "bearer"
    assert caller.session is session
    assert caller.mfa_at_ms is None


@pytest.mark.parametrize("kind", ["service", "unknown", "agent", "system", "", None])
def test_principal_kind_is_never_inferred(kind):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    session = SessionFixture()
    session.actor.actor_type = kind
    http, mcp = adapters(session)
    req = bearer_request((b"authorization", b"Bearer fixture"))
    if kind == "service":
        caller = asyncio.run(http.authenticate(req))
        assert caller == mcp.caller_for_request()
        assert caller.principal_kind == "service"
    else:
        with pytest.raises(HTTPAuthenticationError):
            asyncio.run(http.authenticate(req))
        assert mcp.caller_for_request() is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("principal", "spoofed"),
        ("tenant", "other-tenant"),
        ("scopes", ["fixture:admin"]),
        ("scopes", "fixture:read"),
        ("scopes", [None]),
        ("policy_version", "other-policy"),
        ("delegation", "fabricated"),
    ],
)
def test_mismatched_or_malformed_verified_claims_refuse_both_surfaces(key, value):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    session = SessionFixture()
    session.claims[key] = value
    http, mcp = adapters(session)
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(
            http.authenticate(bearer_request((b"authorization", b"Bearer fixture")))
        )
    assert mcp.caller_for_request() is None


@pytest.mark.parametrize(
    "authority", ["absent", "revoked", "unauthenticated", "tenant_mismatch"]
)
def test_missing_or_unverified_authority_refuses_both_surfaces(authority):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    session = SessionFixture()
    if authority == "absent":
        session = None
    elif authority == "revoked":
        session.revoked = True
    elif authority == "unauthenticated":
        session.actor.authenticated = False
    else:
        session.actor.tenant_id = "other-tenant"
    http, mcp = adapters(session)
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(
            http.authenticate(bearer_request((b"authorization", b"Bearer fixture")))
        )
    assert mcp.caller_for_request() is None


def test_mcp_does_not_cache_authority_after_revocation():
    session = SessionFixture()
    _, mcp = adapters(session)
    assert mcp.caller_for_request() is not None
    session.revoked = True
    assert mcp.caller_for_request() is None


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Basic fixture")],
        [(b"authorization", b"Bearer ")],
        [(b"authorization", b"Bearer fixture extra")],
        [(b"authorization", b"Bearer fixture"), (b"authorization", b"Bearer other")],
    ],
)
def test_http_requires_unambiguous_bearer(headers):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    http, _ = adapters(SessionFixture())
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(http.authenticate(bearer_request(*headers)))


def test_http_unconfigured_authority_fails_closed():
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(
            AmbientHTTPAuthenticator().authenticate(
                bearer_request((b"authorization", b"Bearer fixture"))
            )
        )


@pytest.mark.parametrize("admitted", [True, False])
def test_cookie_and_bearer_cannot_bypass_missing_browser_verification(admitted):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    http, _ = adapters(SessionFixture())
    req = console_request(admitted=admitted)
    req.scope["headers"].append((b"authorization", b"Bearer fixture"))
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(http.authenticate(req))


@pytest.mark.parametrize("csrf_valid", [True, False])
def test_browser_requires_verifier_and_preserves_verified_stepup(
    monkeypatch, csrf_valid
):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    session = SessionFixture()
    calls = []

    async def resolve(req):
        calls.append("session")
        return session

    async def verify(req, bound_session):
        assert bound_session is session
        calls.append("csrf")
        if not csrf_valid:
            raise PermissionError("fixture csrf mismatch")
        return NOW_MS

    auth = AmbientHTTPAuthenticator(
        console_origin=ORIGIN,
        session_for_request=resolve,
        verify_browser_session=verify,
    )
    req = console_request()
    monkeypatch.setattr("graph_os.api.http.auth.time.time", lambda: NOW_MS / 1000)
    if csrf_valid:
        caller = asyncio.run(auth.authenticate(req))
        assert caller.credential_kind == "session"
        assert caller.mfa_at_ms == NOW_MS
        assert auth.is_console_request(req, caller)
    else:
        with pytest.raises(HTTPAuthenticationError):
            asyncio.run(auth.authenticate(req))
    assert calls == ["session", "csrf"]


def test_request_state_does_not_supply_principal_tenant_scopes_or_stepup():
    import asyncio

    session = SessionFixture()
    http, mcp = adapters(session)
    req = bearer_request((b"authorization", b"Bearer fixture"), (b"x-tenant", b"spoof"))
    req.scope["state"].update(
        principal="spoof",
        tenant="spoof",
        scopes=["fixture:admin"],
        graphos_console_mfa_at_ms=NOW_MS,
    )
    caller = asyncio.run(http.authenticate(req))
    assert caller == mcp.caller_for_request()
    assert caller.principal == session.actor.actor_id
    assert caller.mfa_at_ms is None


@pytest.mark.parametrize("revoked", [False, True])
def test_real_http_endpoint_and_mcp_projection_share_verified_context(revoked):
    import asyncio

    from graph_os.api.invoke import OpResult
    from starlette.responses import JSONResponse

    from graph_os.api.http.routes import make_endpoint
    from graph_os.api.mcp.verbs import MCPProjection, dispatch_verb
    from graph_os.api.registry import Surface, Verb

    session = SessionFixture()
    session.revoked = revoked
    http, mcp = adapters(session)
    op = SimpleNamespace(id="fixture.read", verb=Verb.ASK)
    registry = SimpleNamespace(digest="fixture-digest", get=lambda _: op)
    services = SimpleNamespace(registry=registry)
    seen = []

    async def invocation(op_id, params, caller, surface, **kwargs):
        seen.append((caller, surface))
        return OpResult(value={"fixture": True})

    endpoint = make_endpoint(
        op,
        services=services,
        authenticate=http.authenticate,
        invoke=invocation,
        response=lambda outcome, op, request_id: JSONResponse(outcome.value),
        generic=True,
        is_console_request=http.is_console_request,
    )
    projection = MCPProjection(
        registry=registry,
        services=services,
        resolver=SimpleNamespace(scope_ref=lambda *args, **kwargs: "fixture-scope"),
        caller_for_request=mcp.caller_for_request,
        policy_gate=object(),
        invoke=invocation,
    )
    req = bearer_request((b"authorization", b"Bearer fixture"))
    req.scope["method"] = "GET"
    req.scope["query_string"] = b""
    response = asyncio.run(endpoint(req))
    outcome = asyncio.run(dispatch_verb("ask", projection, op=op.id))
    if revoked:
        assert response.status_code == 401
        assert outcome["ok"] is False
        assert seen == []
    else:
        assert response.status_code == 200
        assert outcome["ok"] is True
        assert len(seen) == 2
        assert seen[0][0] == seen[1][0]
        assert [surface for _, surface in seen] == [Surface.HTTP, Surface.MCP]


@pytest.mark.parametrize("mfa", [True, "2000000", 2_000_000.0])
def test_browser_rejects_non_integer_stepup_authority(mfa):
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    async def resolve(req):
        return SessionFixture()

    async def verify(req, session):
        return mfa

    auth = AmbientHTTPAuthenticator(
        session_for_request=resolve, verify_browser_session=verify
    )
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(auth.authenticate(console_request()))


def test_authority_fault_is_not_hidden_as_authentication_denial():
    import asyncio

    from graph_os.api.mcp.caller import VerifiedMCPCaller

    def broken():
        raise RuntimeError("fixture authority unavailable")

    async def resolve(req):
        return broken()

    auth = AmbientHTTPAuthenticator(session_for_request=resolve)
    mcp = VerifiedMCPCaller(
        session_for_request=broken, credential_kind_for_request=lambda: "bearer"
    )
    with pytest.raises(RuntimeError, match="fixture authority unavailable"):
        asyncio.run(
            auth.authenticate(bearer_request((b"authorization", b"Bearer fixture")))
        )
    with pytest.raises(RuntimeError, match="fixture authority unavailable"):
        mcp.caller_for_request()


def test_verified_token_facts_are_not_a_current_engine_session():
    import asyncio

    from graph_os.api.http.auth import HTTPAuthenticationError

    # Explicit partial-result fixture has authenticated token facts but no
    # qualified current policy/delegation or engine envelope.
    partial = SimpleNamespace(
        principal="fixture-human",
        tenant="fixture-tenant",
        principal_kind="human",
        exact_scopes=frozenset({"fixture:read"}),
    )
    http, mcp = adapters(partial)
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(
            http.authenticate(bearer_request((b"authorization", b"Bearer fixture")))
        )
    assert mcp.caller_for_request() is None


def test_bound_browser_verifier_requires_explicit_authority():
    from graph_os.api.http.auth import BoundBrowserVerifier

    with pytest.raises(ValueError, match="authority required"):
        BoundBrowserVerifier(authority=None, console_origin=ORIGIN)


def test_http_local_bearer_bridge_passes_exact_token_to_owner(monkeypatch):
    import asyncio
    import time

    from agent_utilities.security import request_identity

    from graph_os.api.http.auth import verify_local_bearer_request

    now = int(time.time())
    result = request_identity.VerifiedLocalBearer(
        claims={
            "iss": "https://issuer.example.test",
            "aud": "fixture-engine",
            "sub": "fixture-principal",
            "tenant_id": "fixture-tenant",
            "principal_kind": "service",
            "scope": "fixture:read",
            "iat": now,
            "nbf": now - 1,
            "exp": now + 300,
            "jti": "fixture-id",
        },
        issuer="https://issuer.example.test",
        audience="fixture-engine",
    )
    seen = []

    async def verify(token):
        seen.append(token)
        return result

    monkeypatch.setattr(request_identity, "verify_local_bearer_token", verify)
    req = bearer_request((b"authorization", b"Bearer exact-synthetic-token"))
    req.scope["state"] = {"principal": "spoofed", "tenant": "spoofed"}
    assert asyncio.run(verify_local_bearer_request(req)) is result
    assert seen == ["exact-synthetic-token"]
    assert result.principal == "fixture-principal"


def test_http_local_bearer_bridge_rejects_ambiguous_credentials(monkeypatch):
    import asyncio

    from agent_utilities.security import request_identity

    from graph_os.api.http.auth import (
        HTTPAuthenticationError,
        verify_local_bearer_request,
    )

    async def unexpected(token):
        raise AssertionError("ambiguous credentials must not reach verification")

    monkeypatch.setattr(request_identity, "verify_local_bearer_token", unexpected)
    req = bearer_request(
        (b"authorization", b"Bearer first"), (b"authorization", b"Bearer second")
    )
    with pytest.raises(HTTPAuthenticationError):
        asyncio.run(verify_local_bearer_request(req))


def test_bound_browser_verifier_calls_actual_webui_exporter(monkeypatch):
    import asyncio

    from agent_webui.oidc_session import BrowserSessionEvidence

    from graph_os.api.http.auth import BoundBrowserVerifier

    req = console_request()
    session = SessionFixture()
    calls = []

    class AuthorityFixture:
        async def verify_request(self, scope):
            calls.append(scope)
            return BrowserSessionEvidence(
                request_scope=scope,
                subject=session.actor.actor_id,
                tenant=session.tenant,
                session_ref="fixture-session",
                expires_at_ms=NOW_MS + 60_000,
                mfa_at_ms=NOW_MS,
            )

    monkeypatch.setattr("agent_webui.oidc_session.time.time", lambda: NOW_MS / 1000)
    verifier = BoundBrowserVerifier(authority=AuthorityFixture(), console_origin=ORIGIN)
    assert asyncio.run(verifier(req, session)) == NOW_MS
    assert len(calls) == 1 and calls[0] is req.scope
