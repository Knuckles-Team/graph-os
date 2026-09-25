"""Focused HTTP adapter guards; shared invoke/registry integration runs at cutover."""

from __future__ import annotations

import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from graph_os.api.http.app import _subapp_path, create_api_application
from graph_os.api.http.auth import AmbientHTTPAuthenticator, HTTPAuthenticationError
from graph_os.api.http.openapi import document
from graph_os.api.http.protocol_routes import PROTOCOL_ROUTES
from graph_os.api.http.routes import _params, make_endpoint


def request(
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]] = (),
    body: bytes = b"",
    state: dict | None = None,
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
            "state": state or {},
        },
        receive,
    )


@pytest.mark.asyncio
async def test_missing_authority_fails_closed() -> None:
    with pytest.raises(HTTPAuthenticationError):
        await AmbientHTTPAuthenticator().authenticate(
            request("GET", "/api/v1/registry")
        )


@pytest.mark.asyncio
async def test_cookie_requires_gate_admission_and_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoke_module = ModuleType("graph_os.api.invoke")
    session = SimpleNamespace(
        tenant="tenant-a", actor=SimpleNamespace(actor_id="alice")
    )

    class Caller:
        @staticmethod
        def from_session(value, *, request_id, mfa_at_ms=None):
            assert value is session
            return SimpleNamespace(
                principal="alice",
                tenant="tenant-a",
                request_id=request_id,
                mfa_at_ms=mfa_at_ms,
            )

    invoke_module.VerifiedCaller = Caller  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api.invoke", invoke_module)
    from agent_utilities.knowledge_graph.core import session as session_module

    monkeypatch.setattr(session_module, "resolve_session", lambda: session)
    identity_module = ModuleType("graph_os.identity")
    identity_module.__path__ = []  # type: ignore[attr-defined]
    browser_module = ModuleType("graph_os.identity.browser")

    def session_from_scope(scope):
        cookies = [v for k, v in scope["headers"] if k == b"cookie"]
        return "opaque" if cookies == [b"__Host-graphos_session=opaque"] else None

    def csrf_refusal(scope, token):
        assert token == "opaque"
        return (
            None
            if (b"origin", b"https://example.test") in scope["headers"]
            and (b"x-csrf-token", b"secret") in scope["headers"]
            else "csrf_refused"
        )

    browser_module.session_from_scope = session_from_scope  # type: ignore[attr-defined]
    browser_module.csrf_refusal = csrf_refusal  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.identity", identity_module)
    monkeypatch.setitem(sys.modules, "graph_os.identity.browser", browser_module)
    auth = AmbientHTTPAuthenticator()
    cookie = (b"cookie", b"__Host-graphos_session=opaque")
    bearer = (b"authorization", b"Bearer gate-minted")
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(
            request("POST", "/api/v1/ops/example", [cookie, bearer])
        )
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(
            request(
                "POST",
                "/api/v1/ops/example",
                [cookie, bearer],
                state={"graphos_session_admitted": True},
            )
        )
    good = request(
        "POST",
        "/api/v1/ops/example",
        [
            cookie,
            bearer,
            (b"origin", b"https://example.test"),
            (b"x-csrf-token", b"secret"),
        ],
        state={
            "graphos_session_admitted": True,
            "user_claims": {"sub": "alice", "tenant_id": "tenant-a"},
            "graphos_console_mfa_at_ms": int(time.time() * 1000),
        },
    )
    admitted = await auth.authenticate(good)
    assert admitted.principal == "alice"
    assert admitted.mfa_at_ms is not None
    mismatch = request(
        "POST",
        "/api/v1/ops/example",
        [
            cookie,
            bearer,
            (b"origin", b"https://example.test"),
            (b"x-csrf-token", b"secret"),
        ],
        state={
            "graphos_session_admitted": True,
            "user_claims": {"sub": "bob", "tenant_id": "tenant-a"},
        },
    )
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(mismatch)
    duplicate = request(
        "POST",
        "/api/v1/ops/example",
        [
            cookie,
            cookie,
            bearer,
            (b"origin", b"https://example.test"),
            (b"x-csrf-token", b"secret"),
        ],
        state={
            "graphos_session_admitted": True,
            "user_claims": {"sub": "alice", "tenant_id": "tenant-a"},
        },
    )
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(duplicate)
    stale = request(
        "POST",
        "/api/v1/ops/example",
        [
            cookie,
            bearer,
            (b"origin", b"https://example.test"),
            (b"x-csrf-token", b"secret"),
        ],
        state={
            "graphos_session_admitted": True,
            "user_claims": {"sub": "alice", "tenant_id": "tenant-a"},
            "graphos_console_mfa_at_ms": int(time.time() * 1000) - 16 * 60 * 1000,
        },
    )
    assert (await auth.authenticate(stale)).mfa_at_ms is None


@pytest.mark.asyncio
async def test_http_params_refuse_duplicate_and_non_object_body() -> None:
    headers = [(b"content-type", b"application/json")]
    assert await _params(
        request("POST", "/ops/example", headers, b'{"x":1}'), generic=True
    ) == {"x": 1}
    with pytest.raises(ValueError):
        await _params(request("POST", "/ops/example", headers, b"[]"), generic=True)


def test_openapi_contains_only_supplied_ops() -> None:
    model = SimpleNamespace(model_json_schema=lambda: {"type": "object"})
    op = SimpleNamespace(
        id="query.uql", summary="Run UQL", http=None, params=model, result=model
    )
    spec = document([op], digest="abc")
    assert list(spec["paths"]) == ["/api/v1/ops/query.uql"]
    assert spec["x-registry-digest"] == "abc"


def test_protocol_inventory_records_native_surfaces() -> None:
    paths = {item.path for item in PROTOCOL_ROUTES}
    assert {"/a2a", "/mcp", "/health", "/fleet/events", "/scim/v2/{path:path}"} <= paths


def test_v1_subapp_mount_has_one_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("graph_os.api.registry")
    module.Surface = SimpleNamespace(HTTP="http")  # type: ignore[attr-defined]
    module.canonical_op = lambda op: {"id": op.id}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api.registry", module)
    op = SimpleNamespace(
        id="identity.users.list",
        surfaces={"http"},
        http=SimpleNamespace(method="GET", path="/api/v1/identity/users"),
    )

    class Registry:
        digest = "abc"

        def __iter__(self):
            return iter((op,))

        def find(self, caller, *, surface, policy):
            return (op,) if surface == "http" and policy(op, caller) else ()

    class Auth:
        async def authenticate(self, request):
            return SimpleNamespace(authenticated=True)

    async def visibility(op, caller):
        return True

    child = create_api_application(
        services=SimpleNamespace(registry=Registry()),
        visibility=visibility,
        authenticator=Auth(),  # type: ignore[arg-type]
    )
    assert "/identity/users" in {route.path for route in child.routes}
    parent = FastAPI()
    parent.mount("/api/v1", child)
    with TestClient(parent) as client:
        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        assert response.json()["ops"] == [{"id": op.id}]
        assert client.get("/api/v1/api/v1/registry").status_code == 404


def test_resource_path_rejects_other_api_versions() -> None:
    assert _subapp_path("/api/v1/identity/users") == "/identity/users"
    assert _subapp_path("/identity/users") == "/identity/users"
    with pytest.raises(ValueError):
        _subapp_path("/api/v2/identity/users")


@pytest.mark.asyncio
async def test_console_surface_requires_verified_fresh_mfa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []
    invoke_module = ModuleType("graph_os.api.invoke")

    async def invoke(op_id, params, caller, surface, **kwargs):
        seen.append(surface)
        return SimpleNamespace(code="OK", value={"done": True})

    invoke_module.invoke = invoke  # type: ignore[attr-defined]
    registry_module = ModuleType("graph_os.api.registry")
    registry_module.Surface = SimpleNamespace(HTTP="http", CONSOLE="console")  # type: ignore[attr-defined]
    registry_module.Confirm = SimpleNamespace(CONSOLE="console")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api.invoke", invoke_module)
    monkeypatch.setitem(sys.modules, "graph_os.api.registry", registry_module)
    op = SimpleNamespace(
        id="identity.users.admin_reset",
        confirm="console",
        surfaces={"http", "console"},
    )
    caller = SimpleNamespace(request_id="req", principal_kind="human", mfa_at_ms=None)

    async def authenticate(_request):
        return caller

    endpoint = make_endpoint(
        op,
        services=SimpleNamespace(registry=SimpleNamespace(digest="abc")),
        authenticate=authenticate,
        response=lambda result, op, request_id: result,
        generic=True,
    )
    headers = [(b"content-type", b"application/json")]
    await endpoint(request("POST", "/ops/admin_reset", headers, b"{}"))
    caller.mfa_at_ms = int(time.time() * 1000)
    await endpoint(request("POST", "/ops/admin_reset", headers, b"{}"))
    caller.principal_kind = "service"
    await endpoint(request("POST", "/ops/admin_reset", headers, b"{}"))
    assert seen == ["http", "console", "http"]
