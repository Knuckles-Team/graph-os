"""Authenticated Agent Card and JSON-RPC unary A2A transport."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .authority import A2AIdempotencyConflict, A2ATaskNotCancelable
from .models import A2AMessage
from .routing import A2AAssemblyUnavailable
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


async def _invoke_method(
    service: A2AService,
    request: Request,
    method: str,
    raw_params: dict[str, Any],
    request_id: Any,
) -> Any:
    """Validate and invoke one unary method on the shared service."""
    if method == "message/send":
        send_params = _SendParams.model_validate(raw_params)
        key = request.headers.get("Idempotency-Key", "")
        if not key:
            raise ValueError("Idempotency-Key is required")
        return await service.send_message(
            message=send_params.message,
            idempotency_key=key,
            context_budget_tokens=send_params.context_budget_tokens,
        )
    if method == "tasks/get":
        task_params = _TaskParams.model_validate(raw_params)
        result = await service.get_task(task_params.id)
        return result or _error(request_id, -32001, "Task not found", 404)
    if method == "tasks/list":
        list_params = _ListParams.model_validate(raw_params)
        return await service.list_tasks(
            cursor=list_params.cursor, limit=list_params.limit
        )
    if method == "tasks/cancel":
        cancel_params = _TaskParams.model_validate(raw_params)
        return await service.cancel_task(cancel_params.id)
    return _error(request_id, -32601, "Method not found", 404)


def _application_error(request_id: Any, error: Exception) -> JSONResponse:
    """Translate known service failures without echoing caller input."""
    if isinstance(error, A2AIdempotencyConflict):
        return _error(request_id, -32009, str(error), 409)
    if isinstance(error, A2AAssemblyUnavailable):
        return _error(request_id, -32003, str(error), 503)
    if isinstance(error, A2ATaskNotCancelable):
        return _error(request_id, -32002, str(error), 409)
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
        scope = "kg:write" if method in {"message/send", "tasks/cancel"} else "kg:read"
        await authenticator.authenticate(request, scope=scope)
        try:
            result = await _invoke_method(
                service, request, method, raw_params, request_id
            )
        except (
            A2AIdempotencyConflict,
            A2AAssemblyUnavailable,
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
