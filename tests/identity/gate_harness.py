"""A served application behind the identity gate, over the store double."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from graph_os.identity.browser import CSRF_HEADER, SESSION_COOKIE
from graph_os.identity.composition import (
    IdentityDeployment,
    IdentityRuntime,
    build_identity_runtime,
)
from graph_os.identity.issuer import IssuerSettings

from .store_double import SecretsDouble, StoreDouble

__all__ = ["BASE", "Served", "SETUP_CODE", "serve"]

BASE = "https://localhost:8443"
SETUP_CODE = "operator-setup-code"


def _role_of(scopes: Iterable[str]) -> str | None:
    return "admin" if "webui:admin" in set(scopes) else "user"


async def _echo(request: Request) -> JSONResponse:
    claims = getattr(request.state, "user_claims", None)
    return JSONResponse(
        {
            "authorization": request.headers.get("authorization"),
            "sub": (claims or {}).get("sub"),
            "scope": (claims or {}).get("scope"),
            "amr": (claims or {}).get("amr"),
        }
    )


def deployment(
    profile: str = "tiny", *, none_hostname: str | None = None
) -> IdentityDeployment:
    return IdentityDeployment(
        profile=profile,
        seed_mode="none" if profile == "tiny" else "local",
        none_ack=None,
        none_hostname=none_hostname,
        setup_code=SETUP_CODE,
        issuer=IssuerSettings(
            issuer="https://localhost:8443", audience="graph-os-local", tenant="local"
        ),
    )


@dataclass
class Served:
    """The test client plus the store and runtime behind it."""

    client: TestClient
    store: StoreDouble
    runtime: IdentityRuntime
    session: str | None = None
    csrf: str | None = None

    def remember(self, response: Any) -> None:
        """Keep the session cookie and CSRF token a response handed out.

        The client's own cookie jar is cleared: every test states exactly
        which cookie it presents.
        """
        self.client.cookies.clear()
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            if name == SESSION_COOKIE:
                value = rest.split(";", 1)[0]
                self.session = value or None
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if isinstance(body, dict) and body.get("csrf_token"):
            self.csrf = body["csrf_token"]

    def headers(self, *, state_change: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.session:
            headers["cookie"] = f"{SESSION_COOKIE}={self.session}"
        if state_change:
            headers["origin"] = BASE
            if self.csrf:
                headers[CSRF_HEADER.decode()] = self.csrf
        return headers

    def get(self, path: str, **headers: str) -> Any:
        response = self.client.get(path, headers={**self.headers(), **headers})
        self.remember(response)
        return response

    def post(
        self, path: str, body: dict[str, Any] | None = None, **headers: str
    ) -> Any:
        response = self.client.post(
            path,
            json=body or {},
            headers={**self.headers(state_change=True), **headers},
        )
        self.remember(response)
        return response


def serve(store: StoreDouble, profile: str = "tiny", **kwargs: Any) -> Served:
    runtime = build_identity_runtime(
        deployment(profile, **kwargs), secrets=SecretsDouble(), engine=store
    )
    app = Starlette(routes=[Route("/api/echo", _echo, methods=["GET", "POST"])])
    runtime.install(app, _role_of)
    return Served(TestClient(app, base_url=BASE), store, runtime)
