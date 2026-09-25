"""Sign-in, sign-out, first-run setup, registration and passwords (IDM-08).

Every answer that could reveal whether an account exists is uniform: an
unknown user, a wrong password and a disabled account all answer ``bad``
(the engine decides, and runs the same argon2 work either way), and a
password-reset request answers the same thing for any username.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .admission import AdmissionService
from .broker import OpenedSession
from .browser import csrf_token_for, session_cookie_header, session_from_scope
from .engine import IdentityCall, Resolution
from .material import client_ip_prefix
from .modes import mode_banner
from .setup_gate import SetupGate
from .web_common import (
    RouteError,
    caller_graph_session,
    caller_of,
    guarded,
    json_body,
    require_same_origin,
    signed_out,
    string_field,
)

__all__ = ["session_routes"]

RoleOf = Callable[[Iterable[str]], str | None]
#: The optional features the deployment offers (e-mail), for the login page.
Features = Callable[[], Mapping[str, bool]]

_SESSION_MAX_AGE = 7 * 24 * 60 * 60
_RESET_PURPOSES = frozenset({"admin_reset", "password_reset"})
#: A login-page error is one of the brokers' FIXED codes; nothing else is
#: ever reflected into the redirect.
_ERROR_CODE = re.compile(r"^[a-z_]{1,32}$")
_PAGE_OF_PATH = {"/auth/login": "sign-in", "/auth/mfa": "second-factor"}


def _principal_view(resolution: Resolution, role_of: RoleOf) -> dict[str, Any]:
    scopes = sorted(resolution.scopes)
    return {
        "subject": resolution.principal_id,
        "username": resolution.username,
        "roles": scopes,
        "webui_role": role_of(scopes),
        "is_bootstrap": resolution.is_bootstrap,
        "mfa_enrolled": resolution.mfa_enrolled,
        "mfa_required": resolution.mfa_required,
    }


def _with_session_cookie(response: Response, opened: OpenedSession) -> Response:
    if opened.session_token is not None:
        name, value = session_cookie_header(opened.session_token, _SESSION_MAX_AGE)
        response.headers.append(name.decode(), value.decode("latin-1"))
    return response


def _sign_in_answer(opened: OpenedSession) -> Response:
    body: dict[str, Any] = {"outcome": opened.sign_in.outcome}
    if opened.sign_in.retry_after_ms is not None:
        body["retry_after_ms"] = opened.sign_in.retry_after_ms
    if opened.session_token is not None:
        body["csrf_token"] = csrf_token_for(opened.session_token)
    status = 200 if opened.session_token is not None else 401
    if opened.sign_in.outcome == "throttled":
        status = 429
    return _with_session_cookie(JSONResponse(body, status_code=status), opened)


class _SessionRoutes:
    def __init__(
        self,
        admission: AdmissionService,
        setup: SetupGate,
        role_of: RoleOf,
        features: Features,
    ) -> None:
        self._admission = admission
        self._broker = admission.broker
        self._setup = setup
        self._role_of = role_of
        self._features = features

    async def _signed_in_view(self, request: Request) -> dict[str, Any] | None:
        session = session_from_scope(request.scope)
        resolution = await self._broker.resolve_session(session) if session else None
        if session is None or resolution is None:
            return None
        view = _principal_view(resolution, self._role_of)
        view["csrf_token"] = csrf_token_for(session)
        view["authenticated"] = not resolution.session_mfa_pending
        view["second_factor_required"] = resolution.session_mfa_pending
        return view

    async def status(self, request: Request) -> Response:
        """``GET /auth/session`` — the one source the frontend renders."""
        mode = await self._admission.mode()
        if mode is None:
            self._setup.announce()
            return JSONResponse(
                {"authenticated": False, "mode": None, "setup_required": True}
            )
        base = {
            "mode": mode,
            "banner": mode_banner(mode),
            "setup_required": False,
            "features": dict(self._features()),
        }
        view = await self._signed_in_view(request)
        if view is None and mode == "none":
            return await self._bootstrap_status(base)
        config = await self._broker.config()
        base["registration_policy"] = config.get("registration_policy")
        return JSONResponse({**base, **(view or {"authenticated": False})})

    async def _bootstrap_status(self, base: dict[str, Any]) -> Response:
        admitted = await self._admission.bootstrap()
        view = _principal_view(admitted.resolution, self._role_of)
        body = {
            **base,
            **view,
            "authenticated": True,
            "csrf_token": csrf_token_for(admitted.session_token),
        }
        response = JSONResponse(body)
        name, value = session_cookie_header(admitted.session_token, _SESSION_MAX_AGE)
        response.headers.append(name.decode(), value.decode("latin-1"))
        return response

    async def login(self, request: Request) -> Response:
        require_same_origin(request)
        body = await json_body(request)
        opened = await self._broker.sign_in(
            string_field(body, "username") or "",
            string_field(body, "password") or "",
            ip_prefix=client_ip_prefix(request.client.host if request.client else None),
            new_password=string_field(body, "new_password", required=False),
        )
        return _sign_in_answer(opened)

    async def logout(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request, pending_ok=True)
        await self._broker.sign_out(caller.session_token)
        return signed_out(JSONResponse({"signed_out": True}))

    async def setup(self, request: Request) -> Response:
        """First-run: create the first administrator (setup code required)."""
        require_same_origin(request)
        if await self._admission.mode() is not None:
            raise RouteError(409, "already_initialized")
        body = await json_body(request)
        if not self._setup.accepts(string_field(body, "setup_code") or ""):
            raise RouteError(403, "setup_code_invalid")
        username = string_field(body, "username") or ""
        password = string_field(body, "password") or ""
        await self._broker.initialize("local", username, password)
        return _sign_in_answer(await self._broker.sign_in(username, password))

    async def register(self, request: Request) -> Response:
        """Account creation. Registration is administrator-only (ruling)."""
        caller = await caller_of(self._admission, request)
        body = await json_body(request)
        created = {
            "username": string_field(body, "username"),
            "kind": "human",
            "display_name": string_field(body, "display_name", required=False),
            "email": string_field(body, "email", required=False),
            "password": string_field(body, "password", required=False),
            "must_change": True,
        }
        request_body = {
            key: value for key, value in created.items() if value is not None
        }
        reply = await self._broker.engine.as_caller(
            caller_graph_session(self._admission, caller),
            IdentityCall("user", "create", request_body),
        )
        return JSONResponse(reply.expect("principal"), status_code=201)

    async def page(self, request: Request) -> Response:
        """``GET /auth/login`` / ``GET /auth/mfa``: the application's own screen.

        The external brokers finish a sign-in by redirecting here; the SPA
        renders the sign-in or second-factor screen at ``/``. Only a fixed
        error code is carried along.
        """
        target = f"/?auth={_PAGE_OF_PATH.get(request.url.path, 'sign-in')}"
        error = request.query_params.get("error") or ""
        if _ERROR_CODE.match(error):
            target += f"&error={error}"
        return RedirectResponse(
            target, status_code=303, headers={"cache-control": "no-store"}
        )

    async def forgot(self, request: Request) -> Response:
        """Uniform for every username: no mail adapter ⇒ the offline paths."""
        require_same_origin(request)
        return JSONResponse(
            {"email_reset": False, "alternatives": ["recovery_code", "admin_reset"]}
        )

    async def reset(self, request: Request) -> Response:
        require_same_origin(request)
        body = await json_body(request)
        purpose = string_field(body, "purpose") or ""
        if purpose not in _RESET_PURPOSES:
            raise RouteError(400, "purpose_invalid")
        await self._broker.redeem_reset(
            purpose,
            string_field(body, "token") or "",
            string_field(body, "new_password") or "",
        )
        return JSONResponse({"reset": True})

    async def change_password(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request)
        body = await json_body(request)
        await self._broker.change_password(
            caller_graph_session(self._admission, caller),
            string_field(body, "current") or "",
            string_field(body, "new") or "",
        )
        return JSONResponse({"changed": True})


def session_routes(
    admission: AdmissionService, setup: SetupGate, role_of: RoleOf, features: Features
) -> list[Route]:
    routes = _SessionRoutes(admission, setup, role_of, features)
    table = (
        ("/auth/session", routes.status, "GET"),
        ("/auth/login", routes.page, "GET"),
        ("/auth/mfa", routes.page, "GET"),
        ("/auth/login", routes.login, "POST"),
        ("/auth/logout", routes.logout, "POST"),
        ("/auth/setup", routes.setup, "POST"),
        ("/auth/register", routes.register, "POST"),
        ("/auth/password/forgot", routes.forgot, "POST"),
        ("/auth/password/reset", routes.reset, "POST"),
        ("/auth/password/change", routes.change_password, "POST"),
    )
    return [
        Route(path, guarded(handler), methods=[method])
        for path, handler, method in table
    ]
