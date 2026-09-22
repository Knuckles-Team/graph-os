"""GraphOS host-server policy layered over the SDK MCP server factory.

The agent-connector-sdk builds the FastMCP server, its authentication provider,
network hardening, visibility filter and the privacy-safe error and rate-limit
middleware. GraphOS adds the two host concerns that belong to the process that
serves privileged graph tools:

* :class:`VerifiedSessionMiddleware` binds every tool call to the caller's
  verified actor and graph session, minted from the claims FastMCP's auth
  provider already validated. A call with no validated claims proceeds only
  under an already-ambient session or the stdio process authority; otherwise
  it is refused, never run anonymously.
* :func:`register_metrics_route` serves the merged gateway and engine
  Prometheus exposition. A non-loopback listener serves it only behind the
  bearer resolved from ``MCP_METRICS_TOKEN_REF``.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
from collections.abc import Callable
from typing import Any

from agent_connector_sdk.config import setting
from agent_connector_sdk.credentials.references import SecretReferenceError
from agent_connector_sdk.credentials.resolution import resolve_secret_reference
from agent_connector_sdk.credentials.resolver import CredentialUnavailableError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.requests import Request
from starlette.responses import Response

__all__ = [
    "VerifiedSessionMiddleware",
    "metrics_token",
    "register_metrics_route",
]

logger = logging.getLogger(__name__)

_MIN_METRICS_TOKEN = 32
_MAX_METRICS_TOKEN = 4_096


def _validated_claims(context: MiddlewareContext) -> dict[str, Any] | None:
    """The claims FastMCP's auth provider validated for this request, if any."""
    auth = getattr(context, "auth", None)
    claims = getattr(auth, "claims", None)
    if claims:
        return dict(claims)
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    claims = getattr(token, "claims", None) if token is not None else None
    return dict(claims) if claims else None


class VerifiedSessionMiddleware(Middleware):
    """Scope each tool call to the verified caller's actor and graph session."""

    def __init__(self, process_session: Callable[[], Any]) -> None:
        self._process_session = process_session

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        from agent_utilities.api import SessionRequiredError, current_session

        claims = _validated_claims(context)
        if claims:
            return await self._call_as_caller(claims, context, call_next)
        if current_session() is not None or self._process_session() is not None:
            return await call_next(context)
        raise SessionRequiredError(
            "Verified caller identity required: no validated bearer credential "
            "was presented and no local process authority is bound"
        )

    @staticmethod
    async def _call_as_caller(
        claims: dict[str, Any], context: MiddlewareContext, call_next: Any
    ) -> Any:
        from agent_utilities.api import use_session
        from agent_utilities.security.brain_context import use_actor
        from agent_utilities.security.request_identity import (
            actor_from_claims,
            mint_graph_session,
        )

        actor = actor_from_claims(claims)
        try:
            session = mint_graph_session(actor)
            session.engine_verified_context()
        except PermissionError:
            raise PermissionError("Verified graph authority is incomplete") from None
        with use_actor(actor), use_session(session):
            return await call_next(context)


def metrics_token() -> str | None:
    """The bearer guarding a remote ``/metrics`` route, or ``None``."""
    reference = str(setting("MCP_METRICS_TOKEN_REF", "") or "").strip()
    if not reference:
        return None
    try:
        token = resolve_secret_reference(reference)
    except (SecretReferenceError, CredentialUnavailableError) as exc:
        logger.error("MCP_METRICS_TOKEN_REF is unusable; /metrics is disabled: %s", exc)
        return None
    if not _MIN_METRICS_TOKEN <= len(token) <= _MAX_METRICS_TOKEN or any(
        char in token for char in "\r\n\x00"
    ):
        logger.error("MCP_METRICS_TOKEN_REF resolved an invalid token")
        return None
    return token


def _is_loopback(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _authorized(request: Request, token: str | None) -> bool:
    if token is None:
        return True
    values = [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", ())
        if key.lower() == b"authorization"
    ]
    return len(values) == 1 and secrets.compare_digest(values[0], f"Bearer {token}")


def register_metrics_route(mcp: Any, *, transport: str, host: str) -> None:
    """Serve ``/metrics``: always on loopback/stdio, remotely only with a token."""
    remote = transport != "stdio" and not _is_loopback(host)
    token = metrics_token() if remote else None
    if remote and token is None:
        return

    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(request: Request) -> Response:
        if not _authorized(request, token):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        from agent_utilities.observability.gateway_metrics import (
            render_metrics_with_engine,
        )

        body, content_type = await render_metrics_with_engine()
        return Response(
            content=body, media_type=content_type, headers={"Cache-Control": "no-store"}
        )
