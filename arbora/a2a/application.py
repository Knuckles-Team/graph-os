"""Authenticated Agent Card and JSON-RPC unary A2A transport."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from secrets import token_hex
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from arbora.api.http.auth import HTTPAuthenticationError

from .authority import (
    A2AIdempotencyConflict,
    A2AStreamingUnavailable,
    A2ATaskNotCancelable,
    A2ATransitionHistoryUnavailable,
)
from .models import A2AMessage, A2AOperationInvokeParams, A2APlanConfirmParams
from .routing import A2AAssemblyUnavailable, A2AOperationBridgeUnavailable
from .service import A2AService
from .terminal_event import A2ATerminalEventUnavailable

if TYPE_CHECKING:
    from arbora.api.http.auth import AmbientHTTPAuthenticator
    from arbora.api.invoke.pipeline import InvokeServices
    from arbora.api.invoke.steps import VerifiedCaller

__all__ = [
    "A2AAuthenticator",
    "AmbientA2AAuthenticator",
    "create_a2a_application",
    "create_a2a_handlers",
]


@runtime_checkable
class A2AAuthenticator(Protocol):
    async def authenticate(
        self, request: Request, *, scope: str
    ) -> VerifiedCaller | None:
        """Verify authority; an injected adapter may own its dispatch binding."""


class AmbientA2AAuthenticator:
    """Resolve each HTTP request through AU's verified bearer/session flow."""

    async def authenticate(self, request: Request, *, scope: str) -> VerifiedCaller:
        caller = await _request_caller(request)
        return _require_caller_scope(caller, scope)


async def _request_caller(request: Request) -> VerifiedCaller:
    from arbora.api.http.auth import (
        AmbientHTTPAuthenticator,
        verified_bearer_session_for_request,
    )

    return await AmbientHTTPAuthenticator(
        session_for_request=verified_bearer_session_for_request
    ).authenticate(request)


def _require_caller_scope(caller: VerifiedCaller, scope: str) -> VerifiedCaller:
    if scope not in caller.effective_scopes:
        raise PermissionError("Required A2A scope unavailable")
    return caller


async def _refresh_unary_caller(caller: VerifiedCaller | None) -> None:
    """Refuse changed authority before entering the task service's session."""
    if caller is None:
        return
    from arbora.api.invoke.bootstrap import (
        SessionRequiredError,
        verify_current_from_session,
    )
    from arbora.api.invoke.steps import authenticate

    try:
        current = await verify_current_from_session(caller)
    except SessionRequiredError:
        raise HTTPAuthenticationError("Current A2A authority required") from None
    if current != caller or authenticate(current) is not None:
        raise HTTPAuthenticationError("Current A2A authority required")


@contextmanager
def _caller_session_scope(caller: VerifiedCaller | None) -> Iterator[None]:
    """Bind a returned caller through legacy dispatch; never resolve a fallback."""
    if caller is None:
        # Explicit injected authenticators already own their admission/binding.
        yield
        return
    from arbora.api.contracts.session import use_session
    from arbora.security.actor_context import use_actor

    with use_actor(caller.session.actor), use_session(caller.session):
        yield


def _authentication_error(request_id: Any, error: PermissionError) -> JSONResponse:
    status = 401 if isinstance(error, HTTPAuthenticationError) else 403
    return _error(request_id, -32001, "A2A authority refused", status)


class _Params(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class _SendParams(_Params):
    message: A2AMessage
    context_budget_tokens: int | None = Field(
        default=None, alias="contextBudgetTokens", ge=256, le=1_000_000
    )


class _TaskParams(_Params):
    id: str = Field(min_length=1, max_length=80)


class _GetTaskParams(_TaskParams):
    include_committed_event: bool = Field(
        default=False, alias="includeCommittedEvent", strict=True
    )


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
    task_params = _GetTaskParams.model_validate(raw_params)
    result = await service.get_task(
        task_params.id, include_committed_event=task_params.include_committed_event
    )
    return result or _error(request_id, -32001, "Task not found", 404)


def _resubscribe_stub(raw_params: Any) -> Any:
    # Registered in the method table (ARBORA-A2A-R001) but fail-closed:
    # durable streaming has no bounded, restart-safe event-cursor backing
    # yet. Params are still validated so a malformed call is rejected as
    # invalid, not treated as an unreachable method.
    _ResubscribeParams.model_validate(raw_params)
    raise A2AStreamingUnavailable(
        "durable task streaming and resubscribe are not available yet"
    )


def _op_invoke_stub(raw_params: Any) -> Any:
    # Registered in the method table (ARBORA-A2A-R006) but fail-closed:
    # the shared hosted-operation registry's invoke path is not bridged in
    # yet. Params are still validated so a malformed call is rejected as
    # invalid, not treated as an unreachable method.
    A2AOperationInvokeParams.model_validate(raw_params)
    raise A2AOperationBridgeUnavailable(
        "arbora.op/invoke is not bridged to the shared operation registry yet"
    )


def _plan_confirm_stub(raw_params: Any) -> Any:
    # Registered in the method table (ARBORA-A2A-R005/R006) but
    # fail-closed: the signed human approval exchange is not implemented
    # end to end yet, so no approval attempt may succeed.
    from .approval import verify_pending_signature

    confirmation = A2APlanConfirmParams.model_validate(raw_params)
    # No trusted human-key resolver or durable pending/grant authority is wired.
    # A parsed payload or valid detached signature cannot supply that authority.
    check = verify_pending_signature(confirmation, evidence=None)
    raise A2AOperationBridgeUnavailable(
        f"arbora.plan/confirm authority unavailable: {check.value}"
    )


_SYNC_METHODS: dict[str, Callable[[Any], Any]] = {
    "tasks/resubscribe": _resubscribe_stub,
    "arbora.op/invoke": _op_invoke_stub,
    "arbora.plan/confirm": _plan_confirm_stub,
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
    A2ATerminalEventUnavailable: (-32013, 503),
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


def _operation_response(
    outcome: Any, *, op: str, request_id: Any, caller_id: str, digest: str
) -> JSONResponse:
    """Project shared operation outcomes without reclassifying refusals."""
    from arbora.api.errors import a2a_error_status, to_envelope
    from arbora.api.invoke.steps import OpError

    status = 200
    if outcome.code == "OK":
        payload = {
            "ok": True,
            "result": jsonable_encoder(outcome.value),
            "meta": {"registry_digest": digest, "api_version": "v1"},
        }
    else:
        status, payload = to_envelope(
            outcome,
            op=op,
            request_id=caller_id or token_hex(12),
            registry_digest=digest,
        )
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if isinstance(outcome, OpError):
        code, status = a2a_error_status(outcome.code, source=outcome.source)
        body["error"] = {
            "code": code,
            "message": payload["error"]["message"],
            "data": payload,
        }
    else:
        body["result"] = payload
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


async def _invoke_operation(
    request: Request,
    raw_params: dict[str, Any],
    request_id: Any,
    services: InvokeServices,
    authenticator: AmbientHTTPAuthenticator,
) -> JSONResponse:
    from arbora.api.http.auth import HTTPAuthenticationError
    from arbora.api.invoke.pipeline import invoke
    from arbora.api.invoke.steps import OpError, OpResult
    from arbora.api.registry import Surface

    params = A2AOperationInvokeParams.model_validate(raw_params)
    caller_id = ""
    outcome: OpError | OpResult
    try:
        caller = await authenticator.authenticate(request)
    except HTTPAuthenticationError:
        outcome = OpError("UNAUTHENTICATED")
    else:
        # A plan reference cannot substitute for the missing signed A2A exchange.
        if params.plan_ref is not None:
            raise A2AOperationBridgeUnavailable(
                "A2A plan consumption requires the signed approval exchange"
            )
        caller_id = caller.request_id
        outcome = await invoke(
            params.op,
            params.params,
            caller,
            Surface.A2A,
            services=services,
            idempotency_key=params.idempotency_key,
        )
    return _operation_response(
        outcome,
        op=params.op,
        request_id=request_id,
        caller_id=caller_id,
        digest=services.registry.digest,
    )


def _validate_operation_composition(
    services: InvokeServices | None,
    authenticator: AmbientHTTPAuthenticator | None,
) -> None:
    if (services is None) != (authenticator is None):
        raise ValueError(
            "operation services and authenticator must be composed together"
        )


async def _operation_request(
    request: Request,
    raw_params: dict[str, Any],
    request_id: Any,
    services: InvokeServices,
    authenticator: AmbientHTTPAuthenticator,
) -> JSONResponse:
    try:
        return await _invoke_operation(
            request, raw_params, request_id, services, authenticator
        )
    except (A2AOperationBridgeUnavailable, ValidationError) as error:
        return _application_error(request_id, error)


async def _unary_request(
    service: A2AService,
    authenticator: A2AAuthenticator,
    request: Request,
    method: str,
    raw_params: dict[str, Any],
    request_id: Any,
) -> JSONResponse:
    write_methods = {"message/send", "tasks/cancel", "arbora.plan/confirm"}
    scope = "kg:write" if method in write_methods else "kg:read"
    try:
        caller = await authenticator.authenticate(request, scope=scope)
        await _refresh_unary_caller(caller)
        with _caller_session_scope(caller):
            result = await _invoke_method(
                service, request, method, raw_params, request_id
            )
    except PermissionError as error:
        return _authentication_error(request_id, error)
    except (
        A2ATerminalEventUnavailable,
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
    return _unary_response(result, request_id)


def _unary_response(result: Any, request_id: Any) -> JSONResponse:
    if isinstance(result, JSONResponse):
        return result
    payload = result.model_dump(mode="json", by_alias=True)
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": payload},
        headers={"Cache-Control": "no-store"},
    )


async def _agent_card_request(
    service: A2AService, authenticator: A2AAuthenticator, request: Request
) -> JSONResponse:
    try:
        await authenticator.authenticate(request, scope="kg:read")
    except PermissionError as error:
        return _authentication_error(None, error)
    endpoint = str(request.url.replace(path="/a2a", query=""))
    return JSONResponse(
        service.agent_card(endpoint).model_dump(mode="json", by_alias=True),
        headers={"Cache-Control": "no-store"},
    )


def create_a2a_handlers(
    *,
    service: A2AService,
    authenticator: A2AAuthenticator,
    operation_services: InvokeServices | None = None,
    operation_authenticator: AmbientHTTPAuthenticator | None = None,
) -> tuple[Any, Any]:
    """Create route handlers reusable by standalone FastAPI and FastMCP."""
    if not isinstance(authenticator, A2AAuthenticator):
        raise TypeError("authenticator does not implement A2AAuthenticator")
    _validate_operation_composition(operation_services, operation_authenticator)

    async def agent_card(request: Request) -> JSONResponse:
        return await _agent_card_request(service, authenticator, request)

    async def json_rpc(request: Request) -> JSONResponse:
        envelope = await _request_envelope(request)
        if isinstance(envelope, JSONResponse):
            return envelope
        request_id, method, raw_params = envelope
        if method == "arbora.op/invoke" and operation_services is not None:
            assert operation_authenticator is not None
            return await _operation_request(
                request,
                raw_params,
                request_id,
                operation_services,
                operation_authenticator,
            )
        return await _unary_request(
            service, authenticator, request, method, raw_params, request_id
        )

    return agent_card, json_rpc


def create_a2a_application(
    *,
    service: A2AService,
    authenticator: A2AAuthenticator,
    operation_services: InvokeServices | None = None,
    operation_authenticator: AmbientHTTPAuthenticator | None = None,
) -> FastAPI:
    metadata = service.card_metadata
    app = FastAPI(title=f"{metadata.name} A2A", version=metadata.version)
    card_handler, rpc_handler = create_a2a_handlers(
        service=service,
        authenticator=authenticator,
        operation_services=operation_services,
        operation_authenticator=operation_authenticator,
    )
    app.add_api_route("/.well-known/agent-card.json", card_handler, methods=["GET"])
    app.add_api_route("/a2a", rpc_handler, methods=["POST"])
    return app
