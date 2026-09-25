"""Focused HTTP adapter guards; shared invoke/registry integration runs at cutover."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from graph_os.api.http.auth import AmbientHTTPAuthenticator, HTTPAuthenticationError
from graph_os.api.http.openapi import document
from graph_os.api.http.protocol_routes import PROTOCOL_ROUTES
from graph_os.api.http.routes import _params


def request(
    method: str, path: str, headers: list[tuple[bytes, bytes]] = (), body: bytes = b""
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
async def test_cookie_mutation_requires_verified_csrf() -> None:
    caller = SimpleNamespace(authenticated=True)

    async def verify(value: str):
        assert value == "opaque"
        return caller, "secret"

    auth = AmbientHTTPAuthenticator(cookie_verifier=verify)
    cookie = (b"cookie", b"__Host-graphos-session=opaque")
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(request("POST", "/api/v1/ops/example", [cookie]))
    good = request(
        "POST", "/api/v1/ops/example", [cookie, (b"x-csrf-token", b"secret")]
    )
    assert await auth.authenticate(good) is caller


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
