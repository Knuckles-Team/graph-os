"""Second factors, API keys, administrator resets and the issuer endpoints.

MFA is self-service for ordinary users. A privileged account whose group
requires MFA gets a restricted pending session until it enrolls and verifies
a factor. WebAuthn ceremonies verify browser evidence before passing public
credentials to the engine.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .admission import AdmissionService
from .broker import API_KEY_DEFAULT_TTL_MS
from .web_common import (
    RouteError,
    caller_graph_session,
    caller_of,
    guarded,
    json_body,
    string_field,
)
from .web_webauthn import WebauthnCeremonies

__all__ = ["factor_routes", "issuer_routes"]

_DAY_MS = 24 * 60 * 60 * 1000
_MAX_KEY_DAYS = 365
_SECOND_FACTORS = {"totp": False, "recovery": True}


def _scopes(body: dict[str, Any]) -> list[str]:
    scopes = body.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        raise RouteError(400, "scopes_invalid")
    if not all(isinstance(scope, str) and 0 < len(scope) <= 256 for scope in scopes):
        raise RouteError(400, "scopes_invalid")
    return scopes


def _ttl_ms(body: dict[str, Any]) -> int:
    days = body.get("ttl_days")
    if days is None:
        return API_KEY_DEFAULT_TTL_MS
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or not 1 <= days <= _MAX_KEY_DAYS
    ):
        raise RouteError(400, "ttl_days_invalid")
    return days * _DAY_MS


class _FactorRoutes:
    def __init__(self, admission: AdmissionService) -> None:
        self._admission = admission
        self._broker = admission.broker
        self._webauthn = WebauthnCeremonies(admission)

    async def verify(self, request: Request) -> Response:
        """Complete a pending sign-in with a TOTP or a recovery code."""
        caller = await caller_of(self._admission, request, pending_ok=True)
        body = await json_body(request)
        method = string_field(body, "method") or ""
        if method not in _SECOND_FACTORS:
            raise RouteError(400, "method_invalid")
        result = await self._broker.second_factor(
            caller.session_token,
            string_field(body, "code") or "",
            recovery=_SECOND_FACTORS[method],
        )
        status = 200 if result.outcome == "ok" else 401
        return JSONResponse({"outcome": result.outcome}, status_code=status)

    async def enroll_totp(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request, pending_ok=True)
        enrollment = await self._broker.enroll_totp(
            caller.session_token, caller.resolution.username
        )
        return JSONResponse(
            {
                "secret": enrollment.secret,
                "provisioning_uri": enrollment.provisioning_uri,
            },
            headers={"cache-control": "no-store"},
        )

    async def confirm_totp(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request, pending_ok=True)
        body = await json_body(request)
        await self._broker.confirm_totp(
            caller.session_token, string_field(body, "code") or ""
        )
        return JSONResponse({"confirmed": True})

    async def recovery_codes(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request, pending_ok=True)
        codes = await self._broker.regenerate_recovery_codes(caller.session_token)
        return JSONResponse({"codes": codes}, headers={"cache-control": "no-store"})

    async def webauthn(self, request: Request) -> Response:
        step = request.path_params["step"]
        handler = {
            "register": self._webauthn.register,
            "register-complete": self._webauthn.register_complete,
            "authenticate": self._webauthn.authenticate,
            "authenticate-complete": self._webauthn.authenticate_complete,
        }.get(step)
        if handler is None:
            raise RouteError(404, "webauthn_step_unknown")
        return await handler(request)

    async def issue_api_key(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request)
        body = await json_body(request)
        owner = string_field(body, "principal_id", required=False)
        issued = await self._broker.issue_api_key(
            caller.session_token,
            owner or caller.resolution.principal_id,
            _scopes(dict(body)),
            _ttl_ms(dict(body)),
        )
        return JSONResponse(
            {"key_id": issued.key_id, "api_key": issued.presented},
            status_code=201,
            headers={"cache-control": "no-store"},
        )

    async def revoke_api_key(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request)
        key_id = str(request.path_params["key_id"])
        await self._broker.revoke_api_key(
            caller_graph_session(self._admission, caller), key_id
        )
        return JSONResponse({"revoked": True})

    async def admin_reset(self, request: Request) -> Response:
        """An administrator's single-use reset token for another account."""
        caller = await caller_of(self._admission, request)
        body = await json_body(request)
        token = await self._broker.issue_admin_reset(
            caller.session_token, string_field(body, "principal_id") or ""
        )
        return JSONResponse({"token": token}, headers={"cache-control": "no-store"})


def factor_routes(admission: AdmissionService) -> list[Route]:
    routes = _FactorRoutes(admission)
    table = (
        ("/auth/mfa/verify", routes.verify, "POST"),
        ("/auth/mfa/totp/enroll", routes.enroll_totp, "POST"),
        ("/auth/mfa/totp/confirm", routes.confirm_totp, "POST"),
        ("/auth/mfa/recovery-codes", routes.recovery_codes, "POST"),
        ("/auth/mfa/webauthn/{step}", routes.webauthn, "POST"),
        ("/auth/api-keys", routes.issue_api_key, "POST"),
        ("/auth/api-keys/{key_id}", routes.revoke_api_key, "DELETE"),
        ("/auth/admin/reset", routes.admin_reset, "POST"),
    )
    return [
        Route(path, guarded(handler), methods=[method])
        for path, handler, method in table
    ]


def issuer_routes(admission: AdmissionService) -> list[Route]:
    """``/.well-known/*``: the JWKS and OpenID metadata of the local issuer."""
    issuer = admission.broker.issuer

    async def jwks(_: Request) -> Response:
        return JSONResponse(issuer.jwks(), headers={"cache-control": "max-age=60"})

    async def discovery(_: Request) -> Response:
        return JSONResponse(issuer.discovery())

    return [
        Route("/.well-known/jwks.json", jwks, methods=["GET"]),
        Route("/.well-known/openid-configuration", discovery, methods=["GET"]),
    ]
