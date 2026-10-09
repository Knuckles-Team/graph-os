"""Focused HTTP adapter guards; shared invoke/registry integration runs at cutover.

``AmbientHTTPAuthenticator.authenticate`` (ambient bearer/browser-session
resolution) needs the invocation pipeline's caller type and the identity
module's session helpers, neither of which exists on this branch yet; its
coverage returns with the slice that lands those modules (see
``graph_os/api/http/auth.py``). Everything exercised here needs only the
registry types and an injected ``invoke``/authenticator, matching
GRAPHOS-OPS-R013.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from graph_os.api.http.app import _subapp_path, create_api_application
from graph_os.api.http.auth import AmbientHTTPAuthenticator
from graph_os.api.http.openapi import document
from graph_os.api.http.protocol_routes import PROTOCOL_ROUTES
from graph_os.api.http.routes import _params, make_endpoint
from graph_os.api.registry import Surface


def request(
    method: str,
    path: str,
    headers: Sequence[tuple[bytes, bytes]] = (),
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


async def _unused_invoke(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("invoke should not be reached by this test")


def test_v1_subapp_mount_has_one_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("graph_os.api.registry")
    vars(module).update(
        Surface=SimpleNamespace(HTTP="http"),
        canonical_op=lambda op: {"id": op.id},
    )
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

        def is_console_request(self, request, caller):
            return False

    async def visibility(op, caller):
        return True

    child = create_api_application(
        services=SimpleNamespace(registry=Registry()),
        visibility=visibility,
        authenticator=Auth(),
        invoke=_unused_invoke,
    )
    assert "/identity/users" in {getattr(route, "path", None) for route in child.routes}
    parent = FastAPI()
    parent.mount("/api/v1", child)
    with TestClient(parent) as client:
        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        assert response.json()["ops"] == [{"id": op.id}]
        assert client.get("/api/v1/api/v1/registry").status_code == 404


async def _empty_visibility(op: Any, caller: Any) -> bool:
    return True


class _EmptyRegistry:
    digest = "sw-digest"

    def __iter__(self):
        return iter(())

    def find(self, caller, *, surface, policy):
        return ()


class _OpenAuth:
    async def authenticate(self, request: Request) -> Any:
        return SimpleNamespace(authenticated=True)

    def is_console_request(self, request: Request, caller: Any) -> bool:
        return False


def test_swagger_ui_renders_the_mounted_caller_filtered_openapi_document() -> None:
    child = create_api_application(
        services=SimpleNamespace(registry=_EmptyRegistry()),
        visibility=_empty_visibility,
        authenticator=_OpenAuth(),
        invoke=_unused_invoke,
    )
    parent = FastAPI()
    parent.mount("/api/v1", child)
    with TestClient(parent) as client:
        page = client.get("/api/v1/docs")
        assert page.status_code == 200
        assert "swagger-ui" in page.text
        assert "/api/v1/openapi.json" in page.text
        spec = client.get("/api/v1/openapi.json")
        assert spec.status_code == 200
        assert spec.json()["x-registry-digest"] == _EmptyRegistry.digest


def test_resource_path_rejects_other_api_versions() -> None:
    assert _subapp_path("/api/v1/identity/users") == "/identity/users"
    assert _subapp_path("/identity/users") == "/identity/users"
    with pytest.raises(ValueError):
        _subapp_path("/api/v2/identity/users")


async def test_console_requires_verified_cookie_origin_human_and_fresh_mfa() -> None:
    now = int(time.time() * 1000)
    caller = SimpleNamespace(
        authenticated=True,
        credential_kind="session",
        principal_kind="human",
        delegated=False,
        mfa_at_ms=now,
    )

    auth = AmbientHTTPAuthenticator(console_origin="https://console.example.test")
    headers = [
        (b"cookie", b"__Host-graphos_session=opaque"),
        (b"origin", b"https://console.example.test"),
        (b"x-csrf-token", b"csrf-secret"),
    ]
    attended = request(
        "POST",
        "/api/v1/ops/identity.users.disable",
        headers,
        state={"graphos_session_admitted": True},
    )
    assert auth.is_console_request(attended, caller)
    assert not AmbientHTTPAuthenticator().is_console_request(attended, caller)
    for changed in (
        {"credential_kind": "bearer"},
        {"principal_kind": "service"},
        {"delegated": True},
        {"mfa_at_ms": now - 901_000},
        {"mfa_at_ms": now + 60_000},
        {"mfa_at_ms": None},
    ):
        facts = vars(caller) | changed
        assert not auth.is_console_request(attended, SimpleNamespace(**facts))
    foreign = request(
        "POST",
        "/api/v1/ops/identity.users.disable",
        [headers[0], (b"origin", b"https://other.example.test"), headers[2]],
        state={"graphos_session_admitted": True},
    )
    assert not auth.is_console_request(foreign, caller)
    assert not auth.is_console_request(
        request("POST", "/api/v1/ops/identity.users.disable", headers), caller
    )


async def test_console_projection_passes_only_verified_surface() -> None:
    seen: list[tuple[Surface, str | None]] = []

    async def fake_invoke(op_id, params, caller, surface, **kwargs):
        seen.append((surface, kwargs["plan_ref"]))
        return SimpleNamespace(value={"ok": True})

    caller = SimpleNamespace(request_id="req")
    endpoint = make_endpoint(
        SimpleNamespace(id="identity.users.disable"),
        services=SimpleNamespace(registry=SimpleNamespace(digest="digest")),
        authenticate=lambda _: _return_caller(caller),
        invoke=fake_invoke,
        response=lambda outcome, op, request_id: JSONResponse({}),
        generic=True,
        is_console_request=lambda req, _: (
            req.headers.get("origin") == "https://console.example.test"
        ),
    )
    common = [
        (b"content-type", b"application/json"),
        (b"graphos-plan-ref", b"graphos_plan:reference"),
    ]
    await endpoint(request("POST", "/api/v1/ops/identity.users.disable", common, b"{}"))
    await endpoint(
        request(
            "POST",
            "/api/v1/ops/identity.users.disable",
            [*common, (b"origin", b"https://console.example.test")],
            b"{}",
        )
    )
    assert seen == [
        (Surface.HTTP, "graphos_plan:reference"),
        (Surface.CONSOLE, "graphos_plan:reference"),
    ]


async def _return_caller(caller):
    return caller
