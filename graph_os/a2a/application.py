"""Authenticated Agent Card and JSON-RPC unary A2A transport."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .authority import (
    A2AIdempotencyConflict,
    A2AStreamingUnavailable,
    A2ATaskNotCancelable,
    A2ATransitionHistoryUnavailable,
)
from .models import A2AMessage, A2AOperationInvokeParams, A2APlanConfirmParams
from .routing import A2AAssemblyUnavailable, A2AOperationBridgeUnavailable
from .service import A2AService

__all__ = [
    "A2AAuthenticator",
    "AmbientA2AAuthenticator",
    "create_a2a_application",
    "create_a2a_handlers",
]


@runtime_checkable
class A2AAuthenticator(Protocol):
    async def authenticate(self, request: Request, *, scope: str) -> None:
        """Verify caller identity, tenant binding, and required scope."""


class AmbientA2AAuthenticator:
    """Require the request identity middleware's verified GraphSession."""

    async def authenticate(self, request: Request, *, scope: str) -> None:  # noqa: ARG002
        from agent_utilities.api.session import resolve_session

        resolve_session(required_scope=scope)


class _Params(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class _SendParams(_Params):
    message: A2AMessage
    context_budget_tokens: int | None = Field(
        default=None, alias="contextBudgetTokens", ge=256, le=1_000_000
    )


class _TaskParams(_Params):
    id: str = Field(min_length=1, max_length=80)


class _ResubscribeParams(_Params):
    id: str = Field(min_length=1, max_length=80)
    cursor: str | None = Field(default=None, max_length=1024)


class _ListParams(_Params):
    cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=50, ge=1, le=100)


def _error(request_id: Any, code: int, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def _request_envelope(
    request: Request,
) -> tuple[Any, str, dict[str, Any]] | JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(None, -32700, "Parse error")
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        request_id = body.get("id") if isinstance(body, dict) else None
        return _error(request_id, -32600, "Invalid Request")
    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return _error(request_id, -32602, "Invalid params")
    return request_id, method, params


async def _send_message(service: A2AService, request: Request, raw_params: Any) -> Any:
    send_params = _SendParams.model_validate(raw_params)
    key = request.headers.get("Idempotency-Key", "")
    if not key:
        raise ValueError("Idempotency-Key is required")
    return await service.send_message(
        message=send_params.message,
        idempotency_key=key,
        context_budget_tokens=send_params.context_budget_tokens,
    )


async def _get_task(service: A2AService, raw_params: Any, request_id: Any) -> Any:
    task_params = _TaskParams.model_validate(raw_params)
    result = await service.get_task(task_params.id)
    return result or _error(request_id, -32001, "Task not found", 404)


def _resubscribe_stub(raw_params: Any) -> Any:
    # Registered in the method table (GRAPHOS-A2A-R001) but fail-closed:
    # durable streaming has no bounded, restart-safe event-cursor backing
    # yet. Params are still validated so a malformed call is rejected as
    # invalid, not treated as an unreachable method.
    _ResubscribeParams.model_validate(raw_params)
    raise A2AStreamingUnavailable(
        "durable task streaming and resubscribe are not available yet"
    )


def _op_invoke_stub(raw_params: Any) -> Any:
    # Registered in the method table (GRAPHOS-A2A-R006) but fail-closed:
    # the shared hosted-operation registry's invoke path is not bridged in
    # yet. Params are still validated so a malformed call is rejected as
    # invalid, not treated as an unreachable method.
    A2AOperationInvokeParams.model_validate(raw_params)
    raise A2AOperationBridgeUnavailable(
        "graphos.op/invoke is not bridged to the shared operation registry yet"
    )


def _plan_confirm_stub(raw_params: Any) -> Any:
    # Registered in the method table (GRAPHOS-A2A-R005/R006) but
    # fail-closed: the signed human approval exchange is not implemented
    # end to end yet, so no approval attempt may succeed.
    A2APlanConfirmParams.model_validate(raw_params)
    raise A2AOperationBridgeUnavailable(
        "graphos.plan/confirm is not available until the signed approval "
        "exchange is implemented end to end"
    )


_SYNC_METHODS: dict[str, Callable[[Any], Any]] = {
    "tasks/resubscribe": _resubscribe_stub,
    "graphos.op/invoke": _op_invoke_stub,
    "graphos.plan/confirm": _plan_confirm_stub,
}


async def _invoke_method(
    service: A2AService,
    request: Request,
    method: str,
    raw_params: dict[str, Any],
    request_id: Any,
) -> Any:
    """Validate and invoke one unary method on the shared service."""
    if method == "message/send":
        return await _send_message(service, request, raw_params)
    if method == "tasks/get":
        return await _get_task(service, raw_params, request_id)
    if method == "tasks/list":
        list_params = _ListParams.model_validate(raw_params)
        return await service.list_tasks(
            cursor=list_params.cursor, limit=list_params.limit
        )
    if method == "tasks/cancel":
        cancel_params = _TaskParams.model_validate(raw_params)
        return await service.cancel_task(cancel_params.id)
    sync_handler = _SYNC_METHODS.get(method)
    if sync_handler is not None:
        return sync_handler(raw_params)
    return _error(request_id, -32601, "Method not found", 404)


_ERROR_CODES: dict[type[Exception], tuple[int, int]] = {
    A2AIdempotencyConflict: (-32009, 409),
    A2AAssemblyUnavailable: (-32003, 503),
    A2ATaskNotCancelable: (-32002, 409),
    A2AStreamingUnavailable: (-32010, 501),
    A2AOperationBridgeUnavailable: (-32011, 501),
    A2ATransitionHistoryUnavailable: (-32012, 501),
}


def _application_error(request_id: Any, error: Exception) -> JSONResponse:
    """Translate known service failures without echoing caller input."""
    mapped = _ERROR_CODES.get(type(error))
    if mapped is not None:
        code, status = mapped
        return _error(request_id, code, str(error), status)
    # Pydantic errors may echo caller text in ``input_value``. Keep the wire
    # error stable and privacy-safe; details belong in local logs.
    return _error(request_id, -32602, "Invalid params")


def create_a2a_handlers(
    *, service: A2AService, authenticator: A2AAuthenticator
) -> tuple[Any, Any]:
    """Create route handlers reusable by standalone FastAPI and FastMCP."""
    if not isinstance(authenticator, A2AAuthenticator):
        raise TypeError("authenticator does not implement A2AAuthenticator")

    async def agent_card(request: Request) -> JSONResponse:
        await authenticator.authenticate(request, scope="kg:read")
        endpoint = str(request.url.replace(path="/a2a", query=""))
        return JSONResponse(
            service.agent_card(endpoint).model_dump(mode="json", by_alias=True),
            headers={"Cache-Control": "no-store"},
        )

    async def json_rpc(request: Request) -> JSONResponse:
        envelope = await _request_envelope(request)
        if isinstance(envelope, JSONResponse):
            return envelope
        request_id, method, raw_params = envelope
        write_methods = {"message/send", "tasks/cancel", "graphos.plan/confirm"}
        scope = "kg:write" if method in write_methods else "kg:read"
        await authenticator.authenticate(request, scope=scope)
        try:
            result = await _invoke_method(
                service, request, method, raw_params, request_id
            )
        except (
            A2AIdempotencyConflict,
            A2AAssemblyUnavailable,
            A2AOperationBridgeUnavailable,
            A2AStreamingUnavailable,
            A2ATransitionHistoryUnavailable,
            A2ATaskNotCancelable,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            return _application_error(request_id, error)
        if isinstance(result, JSONResponse):
            return result
        payload = result.model_dump(mode="json", by_alias=True)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "result": payload},
            headers={"Cache-Control": "no-store"},
        )

    return agent_card, json_rpc


def create_a2a_application(
    *, service: A2AService, authenticator: A2AAuthenticator
) -> FastAPI:
    metadata = service.card_metadata
    app = FastAPI(title=f"{metadata.name} A2A", version=metadata.version)
    card_handler, rpc_handler = create_a2a_handlers(
        service=service, authenticator=authenticator
    )
    app.add_api_route("/.well-known/agent-card.json", card_handler, methods=["GET"])
    app.add_api_route("/a2a", rpc_handler, methods=["POST"])
    return app
