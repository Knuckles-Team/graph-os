"""One HTTP endpoint factory for generic and resource-style operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from secrets import token_hex
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from graph_os.api.registry import Invoke

from .auth import HTTPAuthenticationError

MAX_BODY_BYTES = 1_048_576
MAX_KEY_LENGTH = 256


def _error(code: str, *, digest: str, op: str, request_id: str = "") -> JSONResponse:
    from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal, to_envelope

    status, envelope = to_envelope(
        GraphOSRefusal(GraphOSErrorCode(code)),
        op=op,
        request_id=request_id or token_hex(12),
        registry_digest=digest,
    )
    return JSONResponse(
        envelope, status_code=status, headers={"Cache-Control": "no-store"}
    )


async def _json_body_params(request: Request) -> dict[str, Any]:
    """Parse and bound a non-GET request's JSON object body."""

    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ValueError("JSON content type required")
    length = request.headers.get("content-length")
    if length is not None and int(length) > MAX_BODY_BYTES:
        raise ValueError("Body too large")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("Body too large")
    import json

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Object body required")
    return parsed


def _merge_resource_params(request: Request, data: dict[str, Any]) -> None:
    """Add a resource-style endpoint's query and path parameters in place."""

    for name, value in request.query_params.multi_items():
        if name in data or len(request.query_params.getlist(name)) != 1:
            raise ValueError("Duplicate parameter")
        data[name] = value
    for name, value in request.path_params.items():
        if name in data:
            raise ValueError("Duplicate parameter")
        data[name] = value


async def _params(request: Request, *, generic: bool) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if request.method != "GET":
        data.update(await _json_body_params(request))
    if not generic:
        _merge_resource_params(request, data)
    return data


def _invalid_header(value: str | None) -> bool:
    """A present idempotency or plan-ref header must be nonempty and bounded."""

    return value is not None and (not value or len(value) > MAX_KEY_LENGTH)


def _surface_for(
    request: Request,
    caller: Any,
    is_console_request: Callable[[Request, Any], bool] | None,
) -> Any:
    from graph_os.api.registry import Surface

    if is_console_request is not None and is_console_request(request, caller):
        return Surface.CONSOLE
    return Surface.HTTP


def make_endpoint(
    op: Any,
    *,
    services: Any,
    authenticate: Callable[[Request], Awaitable[Any]],
    invoke: Invoke,
    response: Callable[[Any, Any, str], JSONResponse],
    generic: bool,
    is_console_request: Callable[[Request, Any], bool] | None = None,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Return the single handler used by every registered HTTP operation.

    ``invoke`` is the single invocation chokepoint (GRAPHOS-OPS-R007),
    supplied by the server composition root; this module never imports it
    directly so it does not need that pipeline to exist to be importable.
    """

    async def endpoint(request: Request) -> JSONResponse:
        digest = services.registry.digest
        try:
            caller = await authenticate(request)
        except HTTPAuthenticationError:
            return _error("UNAUTHENTICATED", digest=digest, op=op.id)
        try:
            params = await _params(request, generic=generic)
        except (ValueError, TypeError):
            return _error("INVALID_ARGUMENT", digest=digest, op=op.id)
        key = request.headers.get("idempotency-key")
        plan_ref = request.headers.get("graphos-plan-ref")
        if _invalid_header(key) or _invalid_header(plan_ref):
            return _error("INVALID_ARGUMENT", digest=digest, op=op.id)
        outcome = await invoke(
            op.id,
            params,
            caller,
            _surface_for(request, caller, is_console_request),
            services=services,
            plan_ref=plan_ref,
            idempotency_key=key,
        )
        return response(outcome, op, caller.request_id)

    endpoint.__name__ = f"http_{op.id.replace('.', '_')}"
    return endpoint
