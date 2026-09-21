"""Process binding and governed caller adapter for the one browser-control service."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal

from agent_utilities.knowledge_graph.core.session import resolve_session
from pydantic import BaseModel

from graph_os.browser_control.browser_control_api import (
    BrowserCallRequest,
    CancelCallRequest,
    IssueLeaseRequest,
    ReconcileCallRequest,
    RevokeLeaseRequest,
)

BrowserControlAction = Literal[
    "issue_lease",
    "execute_call",
    "cancel_call",
    "reconcile_call",
    "revoke_lease",
]

_binding_lock = threading.RLock()
_service: Any | None = None
_owner_loop: asyncio.AbstractEventLoop | None = None


def register_browser_control_service(service: Any) -> None:
    """Register the exact service injected into agent-webui."""

    global _owner_loop, _service
    with _binding_lock:
        if _service is service:
            return
        if _service is not None:
            raise RuntimeError("a different live browser-control service is registered")
        _service = service
        _owner_loop = None


def unregister_browser_control_service(service: Any) -> None:
    """Release only the exact service whose owning application is stopping."""

    global _owner_loop, _service
    with _binding_lock:
        if _service is not service:
            raise RuntimeError("browser-control service unregister identity changed")
        _service = None
        _owner_loop = None


def bind_browser_control_owner(service: Any) -> None:
    """Bind a registered service to agent-webui's current event loop."""

    global _owner_loop
    loop = asyncio.get_running_loop()
    with _binding_lock:
        if _service is None:
            return
        if _service is not service:
            raise RuntimeError("browser-control owner does not match registration")
        if (
            _owner_loop is not None
            and _owner_loop is not loop
            and not _owner_loop.is_closed()
        ):
            raise RuntimeError("browser-control service already has a live owner loop")
        _owner_loop = loop


def _registered_authority() -> tuple[Any, asyncio.AbstractEventLoop]:
    with _binding_lock:
        service = _service
        loop = _owner_loop
    if service is None or loop is None or loop.is_closed() or not loop.is_running():
        raise RuntimeError("browser-control service is not attached to a live WebUI")
    return service, loop


async def _invoke(method: str, request: BaseModel) -> BaseModel:
    service, owner_loop = _registered_authority()
    operation = getattr(service, method, None)
    if not callable(operation):
        raise RuntimeError("browser-control service contract is incomplete")
    if asyncio.get_running_loop() is owner_loop:
        return await operation(request)
    future = asyncio.run_coroutine_threadsafe(operation(request), owner_loop)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        future.cancel()
        raise


async def dispatch_browser_control(
    action: BrowserControlAction, payload: dict[str, Any]
) -> BaseModel:
    """Authorize and invoke one caller action on the WebUI-owned service."""

    resolve_session(required_scope="kg:write")
    contracts: dict[str, tuple[type[BaseModel], str]] = {
        "issue_lease": (IssueLeaseRequest, "issue_lease"),
        "execute_call": (BrowserCallRequest, "execute_call"),
        "cancel_call": (CancelCallRequest, "cancel_call"),
        "reconcile_call": (ReconcileCallRequest, "reconcile_call"),
        "revoke_lease": (RevokeLeaseRequest, "revoke_lease"),
    }
    contract = contracts.get(action)
    if contract is None:
        raise ValueError("unsupported browser-control action")
    model, method = contract
    request = model.model_validate(payload)
    return await _invoke(method, request)


__all__ = [
    "BrowserControlAction",
    "bind_browser_control_owner",
    "dispatch_browser_control",
    "register_browser_control_service",
    "unregister_browser_control_service",
]
