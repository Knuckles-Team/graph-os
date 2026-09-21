"""Authenticated Agent Card and JSON-RPC unary A2A transport."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from fasta2a.schema import Skill
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .service import A2AIdempotencyConflict, A2AService

__all__ = ["A2AAuthenticator", "create_a2a_application"]


@runtime_checkable
class A2AAuthenticator(Protocol):
    async def authenticate(self, request: Request) -> None:
        """Verify caller identity, tenant binding, and A2A scopes or fail closed."""


def _error(request_id: Any, code: int, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
        status_code=status,
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


def create_a2a_application(
    *,
    service: A2AService,
    authenticator: A2AAuthenticator,
    name: str,
    description: str,
    version: str,
    endpoint_url: str,
    skills: Sequence[Skill] = (),
) -> FastAPI:
    """Build the unary-only boundary. Streaming and push are truthfully disabled."""
    if not isinstance(authenticator, A2AAuthenticator):
        raise TypeError("authenticator does not implement A2AAuthenticator")
    app = FastAPI(title=f"{name} A2A", version=version)

    async def authorize(request: Request) -> None:
        await authenticator.authenticate(request)

    async def send_message(request: Request, params: dict[str, Any]) -> Any:
        key = request.headers.get("Idempotency-Key", "")
        if not key:
            raise ValueError("Idempotency-Key is required")
        return await service.send_message(
            message=params["message"],
            context_id=params.get("contextId") or params.get("context_id"),
            idempotency_key=key,
        )

    async def get_task(_request: Request, params: dict[str, Any]) -> Any:
        return await service.get_task(str(params.get("id") or ""))

    async def list_tasks(_request: Request, params: dict[str, Any]) -> Any:
        tasks, cursor = await service.list_tasks(
            cursor=params.get("cursor"), limit=int(params.get("limit", 50))
        )
        return {"tasks": tasks, "nextCursor": cursor}

    async def cancel_task(_request: Request, params: dict[str, Any]) -> Any:
        return await service.cancel_task(str(params.get("id") or ""))

    handlers = {
        "message/send": send_message,
        "tasks/get": get_task,
        "tasks/list": list_tasks,
        "tasks/cancel": cancel_task,
    }

    @app.get("/.well-known/agent-card.json")
    async def agent_card(request: Request) -> dict[str, Any]:
        await authorize(request)
        return {
            "name": name,
            "description": description,
            "version": version,
            "url": endpoint_url,
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": list(skills),
        }

    @app.post("/a2a")
    async def json_rpc(request: Request) -> JSONResponse:
        await authorize(request)
        envelope = await _request_envelope(request)
        if isinstance(envelope, JSONResponse):
            return envelope
        request_id, method, params = envelope
        handler = handlers.get(method)
        if handler is None:
            return _error(request_id, -32601, "Method not found", 404)
        try:
            result = await handler(request, params)
        except A2AIdempotencyConflict as exc:
            return _error(request_id, -32009, str(exc), 409)
        except (KeyError, TypeError, ValueError) as exc:
            return _error(request_id, -32602, str(exc))
        if result is None and method == "tasks/get":
            return _error(request_id, -32001, "Task not found", 404)
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    return app
