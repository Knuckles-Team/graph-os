"""Standalone `/api/v1` application, mounted by the later cutover lane."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from secrets import token_hex
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .auth import AmbientHTTPAuthenticator, HTTPAuthenticationError
from .openapi import document
from .routes import _error, make_endpoint

Visibility = Callable[[Any, Any], Awaitable[bool]]


def _outcome_response(
    outcome: Any, op: Any, *, digest: str, request_id: str
) -> JSONResponse:
    from graph_os.api.errors import to_envelope

    if hasattr(outcome, "code") and outcome.code != "OK":
        status, payload = to_envelope(
            outcome,
            op=op.id,
            request_id=request_id or token_hex(12),
            registry_digest=digest,
        )
        return JSONResponse(
            payload, status_code=status, headers={"Cache-Control": "no-store"}
        )
    value = getattr(outcome, "value", outcome)
    meta = {"registry_digest": digest, "api_version": "v1"}
    if hasattr(outcome, "meta") and isinstance(outcome.meta, dict):
        meta.update(outcome.meta)
    return JSONResponse(
        {"ok": True, "result": jsonable_encoder(value), "meta": meta},
        headers={"Cache-Control": "no-store"},
    )


def create_api_application(
    *,
    services: Any,
    visibility: Visibility,
    authenticator: AmbientHTTPAuthenticator | None = None,
) -> FastAPI:
    """Create an isolated sub-app with explicit shared authority dependencies.

    ``visibility`` must make the same policy decision as `invoke` for a caller.
    The composition root supplies it; a missing policy is never treated as an
    allow decision. No legacy GraphOS route is mounted here.
    """
    from graph_os.api.registry import Surface, canonical_op

    registry = services.registry
    auth = authenticator or AmbientHTTPAuthenticator()
    authenticate = auth.authenticate
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    async def visible(request: Request) -> tuple[Any, ...] | JSONResponse:
        try:
            caller = await authenticate(request)
        except HTTPAuthenticationError:
            return _error("UNAUTHENTICATED", digest=registry.digest, op="api.registry")
        allowed: dict[str, bool] = {}
        for op in registry:
            try:
                allowed[op.id] = bool(await visibility(op, caller))
            except Exception:  # fail closed on a policy service failure
                allowed[op.id] = False
        return registry.find(
            caller,
            surface=(
                Surface.CONSOLE
                if auth.is_console_request(request, caller)
                else Surface.HTTP
            ),
            policy=lambda op, _: allowed.get(op.id, False),
        )

    @app.get("/api/v1/registry", include_in_schema=False)
    async def registry_route(request: Request) -> JSONResponse:
        ops = await visible(request)
        if isinstance(ops, JSONResponse):
            return ops
        return JSONResponse(
            {
                "api_version": "v1",
                "registry_digest": registry.digest,
                "ops": [canonical_op(op) for op in ops],
            },
            headers={
                "ETag": f'"{registry.digest}"',
                "Cache-Control": "private, no-store",
            },
        )

    @app.get("/api/v1/ops/{op_id}", include_in_schema=False)
    async def op_route(request: Request, op_id: str) -> JSONResponse:
        ops = await visible(request)
        if isinstance(ops, JSONResponse):
            return ops
        for op in ops:
            if op.id == op_id:
                return JSONResponse(
                    canonical_op(op),
                    headers={
                        "ETag": f'"{registry.digest}"',
                        "Cache-Control": "private, no-store",
                    },
                )
        return _error("UNKNOWN_OP", digest=registry.digest, op=op_id)

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    async def openapi_route(request: Request) -> JSONResponse:
        ops = await visible(request)
        if isinstance(ops, JSONResponse):
            return ops
        return JSONResponse(
            document(ops, digest=registry.digest),
            headers={
                "ETag": f'"{registry.digest}"',
                "Cache-Control": "private, no-store",
            },
        )

    for op in registry:
        if Surface.HTTP not in op.surfaces:
            continue
        app.add_api_route(
            f"/api/v1/ops/{op.id}",
            make_endpoint(
                op,
                services=services,
                authenticate=authenticate,
                response=lambda result, current_op, request_id: _outcome_response(
                    result, current_op, digest=registry.digest, request_id=request_id
                ),
                generic=True,
                is_console_request=auth.is_console_request,
            ),
            methods=["POST"],
            include_in_schema=False,
        )
        if op.http is not None:
            app.add_api_route(
                op.http.path,
                make_endpoint(
                    op,
                    services=services,
                    authenticate=authenticate,
                    response=lambda result, current_op, request_id: _outcome_response(
                        result,
                        current_op,
                        digest=registry.digest,
                        request_id=request_id,
                    ),
                    generic=False,
                    is_console_request=auth.is_console_request,
                ),
                methods=[str(op.http.method).upper()],
                include_in_schema=False,
            )
    return app
