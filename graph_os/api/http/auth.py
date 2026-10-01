"""HTTP identity adapter. Only server-verified authority reaches ``invoke``.

Resolving ambient bearer/browser-session authority into a verified caller
needs both the invocation pipeline's caller type and the identity module's
session helpers. Neither ships on this branch yet (see
``specs/hosted-api-operations/requirements.md`` GRAPHOS-OPS-R013), so that
half stays unavailable here rather than being faked: a surface adapter that
resolves ambient authority lands with those modules. ``AmbientHTTPAuthenticator``
keeps only the console-origin/freshness check below, which needs no caller
construction and so has no such dependency; ``create_api_application`` takes
its authenticator as a required, structurally-typed dependency so this
package never imports an authority resolver that does not exist yet.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request


class HTTPAuthenticationError(PermissionError):
    """A request has no verified HTTP identity or a valid CSRF proof."""


class AmbientHTTPAuthenticator:
    """Decide console eligibility for an already-verified caller.

    The gate's session marker is set only after a live browser session and
    token-bound CSRF have been checked. A cookie value cannot set the marker.
    """

    def __init__(self, *, console_origin: str | None = None) -> None:
        # Configured at the server composition root, never from a request.
        self.console_origin = console_origin

    def is_console_request(self, request: Request, caller: Any) -> bool:
        """Only a fresh attended browser session may use Surface.CONSOLE."""
        state = request.scope.get("state") or {}
        if state.get("graphos_session_admitted") is not True:
            return False
        if not request.cookies.get("__Host-graphos_session"):
            return False
        if (
            not self.console_origin
            or request.headers.get("origin") != self.console_origin
        ):
            return False
        if (
            caller.credential_kind != "session"
            or caller.principal_kind != "human"
            or caller.delegated
            or caller.mfa_at_ms is None
        ):
            return False
        age_ms = int(time.time() * 1000) - caller.mfa_at_ms
        return 0 <= age_ms <= 900_000
