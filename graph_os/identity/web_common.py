"""Shared plumbing of the identity HTTP routes: bodies, refusals, the caller."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .admission import AdmissionService
from .browser import clear_cookie_header, csrf_refusal, origin_refusal, session_from_scope
from .engine import IdentityRefused, IdentityUnavailable, Resolution
from .principal_session import session_for

__all__ = [
    "Caller",
    "RouteError",
    "caller_of",
    "guarded",
    "json_body",
    "caller_graph_session",
    "require_same_origin",
    "signed_out",
    "string_field",
]

_MAX_BODY_BYTES = 16 * 1024

#: Engine refusal → HTTP status. Anything unlisted is a 400.
_REFUSAL_STATUS: Mapping[str, int] = {
    "IDENTITY_NOT_AUTHORIZED": 403,
    "IDENTITY_CLASS_VIOLATION": 403,
    "IDENTITY_NOT_FOUND": 404,
    "IDENTITY_NOT_INITIALIZED": 409,
    "IDENTITY_ALREADY_INITIALIZED": 409,
    "IDENTITY_COLLISION": 409,
    "IDENTITY_EPOCH_CONFLICT": 409,
    "IDENTITY_PRECONDITION_FAILED": 409,
    "IDENTITY_ILLEGAL_TRANSITION": 409,
    "IDENTITY_FULL": 429,
}


class RouteError(Exception):
    """A route's refusal: an HTTP status and a stable machine-readable reason."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Caller:
    """The signed-in principal of a cookie-authenticated request."""

    session_token: str
    resolution: Resolution


Handler = Callable[[Request], Awaitable[Response]]


def guarded(handler: Handler) -> Handler:
    """Map route and engine refusals to JSON; never reflect exception text."""

    async def endpoint(request: Request) -> Response:
        try:
            return await handler(request)
        except RouteError as error:
            return JSONResponse({"error": error.reason}, status_code=error.status)
        except IdentityRefused as refused:
            status = _REFUSAL_STATUS.get(refused.code, 400)
            return JSONResponse({"error": refused.code.lower()}, status_code=status)
        except IdentityUnavailable:
            return JSONResponse({"error": "identity_unavailable"}, status_code=503)

    return endpoint


async def json_body(request: Request) -> Mapping[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise RouteError(413, "body_too_large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise RouteError(400, "body_not_json") from None
    if not isinstance(body, dict):
        raise RouteError(400, "body_not_object")
    return body


def string_field(body: Mapping[str, Any], name: str, *, required: bool = True) -> str | None:
    value = body.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise RouteError(400, f"{name}_invalid")
    return value


def require_same_origin(request: Request) -> None:
    """A state change with no session yet still needs a same-origin Origin."""
    reason = origin_refusal(request.scope)
    if reason is not None:
        raise RouteError(403, reason)


async def caller_of(admission: AdmissionService, request: Request, *, pending_ok: bool = False) -> Caller:
    """The cookie session's principal, CSRF-checked; 401 without one.

    ``pending_ok`` admits a session that still owes its second factor (the
    factor routes themselves).
    """
    session = session_from_scope(request.scope)
    if session is None:
        raise RouteError(401, "not_signed_in")
    reason = csrf_refusal(request.scope, session)
    if reason is not None:
        raise RouteError(403, reason)
    resolution = await admission.broker.resolve_session(session)
    if resolution is None:
        raise RouteError(401, "session_expired")
    if resolution.session_mfa_pending and not pending_ok:
        raise RouteError(401, "second_factor_required")
    return Caller(session, resolution)


def caller_graph_session(admission: AdmissionService, caller: Caller) -> Any:
    """The caller's own verified graph session (admin and self-service ops)."""
    return session_for(admission.broker, caller.resolution, ("session",))


def signed_out(response: Response) -> Response:
    name, value = clear_cookie_header()
    response.headers.append(name.decode(), value.decode("latin-1"))
    return response
