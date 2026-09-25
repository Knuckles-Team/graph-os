"""One HTTP endpoint factory for generic and resource-style operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from secrets import token_hex
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

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


async def _params(request: Request, *, generic: bool) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if request.method != "GET":
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
        data.update(parsed)
    if not generic:
        for name, value in request.query_params.multi_items():
            if name in data:
                raise ValueError("Duplicate parameter")
            if (
                name in request.query_params
                and len(request.query_params.getlist(name)) != 1
            ):
                raise ValueError("Duplicate parameter")
            data[name] = value
        for name, value in request.path_params.items():
            if name in data:
                raise ValueError("Duplicate parameter")
            data[name] = value
    return data


def make_endpoint(
    op: Any,
    *,
    services: Any,
    authenticate: Callable[[Request], Awaitable[Any]],
    response: Callable[[Any, Any, str], JSONResponse],
    generic: bool,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Return the single handler used by every registered HTTP operation."""

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
        if (key is not None and (not key or len(key) > MAX_KEY_LENGTH)) or (
            plan_ref is not None and (not plan_ref or len(plan_ref) > MAX_KEY_LENGTH)
        ):
            return _error("INVALID_ARGUMENT", digest=digest, op=op.id)
        from graph_os.api.invoke import invoke
        from graph_os.api.registry import Surface

        outcome = await invoke(
            op.id,
            params,
            caller,
            Surface.HTTP,
            services=services,
            plan_ref=plan_ref,
            idempotency_key=key,
        )
        return response(outcome, op, caller.request_id)

    endpoint.__name__ = f"http_{op.id.replace('.', '_')}"
    return endpoint
