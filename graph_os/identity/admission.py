"""The cookie-to-token-to-caller producer path, entered only by composition."""

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from .broker import GraphOSBrowserAuthority
from .browser import _headers, _snapshot, require_mutation_proof, session_from_scope
from .engine import IdentityUnavailable
from .issuer import LocalIssuer


class BrowserAdmission:
    """Create exact pairings; never accept arbitrary caller/token pairs from HTTP.

    The injected session factory is C/E's qualified token-to-current-authority
    conversion, not actor_from_claims. Missing conversion keeps admission
    unavailable. The returned scope is the sole scope handed to C and dispatch.
    """

    def __init__(
        self,
        issuer: LocalIssuer,
        browser: GraphOSBrowserAuthority,
        session_for_token: Callable[[str], Awaitable[Any]],
        *,
        trusted_origin: str,
    ) -> None:
        if not isinstance(issuer, LocalIssuer) or not isinstance(
            browser, GraphOSBrowserAuthority
        ):
            raise IdentityUnavailable(
                "one configured issuer and browser owner required"
            )
        if not callable(session_for_token):
            raise IdentityUnavailable("qualified token-to-caller authority required")
        self._issuer = issuer
        self._browser = browser
        self._session_for_token = session_for_token
        self._trusted_origin = trusted_origin

    @asynccontextmanager
    async def request(self, scope: Mapping[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Own normalization and private binding for this request's whole lifetime."""
        if _headers(scope, b"authorization"):
            raise PermissionError("incoming cookie and bearer are ambiguous")
        require_mutation_proof(scope, trusted_origin=self._trusted_origin)
        credential = session_from_scope(scope)
        if credential is None:
            raise PermissionError("opaque GraphOS session required")
        key = secrets.token_bytes(32)
        initial = _snapshot(scope, key)
        token = await self._issuer.issue(credential)
        if _snapshot(scope, key) != initial:
            raise PermissionError("request changed during local issuance")
        session = await self._session_for_token(token)
        if _snapshot(scope, key) != initial:
            raise PermissionError("request changed during caller resolution")
        state = dict(scope.get("state") or {})
        for name in (
            "graphos_session_admitted",
            "graphos_identity_session_token",
            "graphos_console_mfa_at_ms",
            "user_claims",
        ):
            state.pop(name, None)
        forwarded = {
            **scope,
            "headers": [
                *scope["headers"],
                (b"authorization", f"Bearer {token}".encode()),
            ],
            "state": state,
        }
        async with self._browser._bind_request(
            forwarded, session, forwarded_token=token
        ):
            # Existing C routing hint only; actual verification uses private owner
            # bindings and ignores all request-state authority assertions.
            forwarded["state"]["graphos_session_admitted"] = True
            yield forwarded
