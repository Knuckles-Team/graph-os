"""Request-local browser producer over explicitly injected qualified owners.

No EG session wire adapter is fabricated here. An unavailable session owner
refuses; the public output is the existing WebUI BrowserSessionEvidence class.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .browser import (
    _headers,
    _RequestBinding,
    _snapshot,
    require_mutation_proof,
    session_from_scope,
)
from .engine import IdentityUnavailable
from .issuer import require_token_binding
from .ports import SessionAuthority, SessionState, VerifiedToken, VerifyToken


def _require_caller(session: Any, state: SessionState) -> None:
    ensure_current = getattr(session, "ensure_authority_current", None)
    get_context = getattr(session, "engine_verified_context", None)
    if not callable(ensure_current) or not callable(get_context):
        raise IdentityUnavailable("qualified current caller session required")
    ensure_current()
    actor = session.actor
    actor.ensure_credential_current()
    resolution = state.resolution
    if not _actor_matches(actor, resolution) or not _session_matches(
        session, actor, resolution
    ):
        raise PermissionError("browser and caller authority disagree")
    context = get_context()
    if not isinstance(context, Mapping):
        raise IdentityUnavailable("qualified caller engine context required")
    for name, expected in resolution.request_context.items():
        if _context_value(name, context.get(name), expected) is False:
            raise PermissionError("browser and caller engine context disagree")


def _actor_matches(actor: Any, resolution: Any) -> bool:
    return (
        actor.authenticated is True
        and actor.actor_id == resolution.principal_id
        and actor.actor_type == resolution.kind
        and actor.tenant_id == resolution.request_context["tenant"]
    )


def _session_matches(session: Any, actor: Any, resolution: Any) -> bool:
    return (
        session.tenant == actor.tenant_id
        and session.policy_version == resolution.request_context["policy_version"]
        and frozenset(session.scopes) == frozenset(resolution.scopes)
    )


def _context_value(name: str, actual: Any, expected: Any) -> bool:
    if name in {"roles", "scopes", "delegation"}:
        if not isinstance(actual, (tuple, list, set, frozenset)) or type(
            actual
        ) not in (tuple, list, set, frozenset):
            raise PermissionError("browser and caller engine context disagree")
        actual = tuple(actual)
        if name != "delegation":
            actual, expected = tuple(sorted(actual)), tuple(sorted(expected))
    return bool(actual == expected)


@dataclass(frozen=True, slots=True, repr=False)
class _BoundRequest:
    scope: Any
    session: Any
    credential: str
    forwarded_token: str
    binding: _RequestBinding
    session_ref: str
    task: Any


class GraphOSBrowserAuthority:
    """Implement C's existing scope-only port with private exact-pair bindings.

    E must bind a qualified caller/token once through BrowserAdmission, supply THIS
    instance's session_for_request to C and call before_invocation immediately
    before dispatch. No request state can create a binding. The context manager
    owns cleanup; a retained public evidence object does not extend its life.
    """

    def __init__(
        self,
        authority: SessionAuthority,
        verify_token: VerifyToken,
        *,
        trusted_origin: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(getattr(authority, "resolve_session", None)) or not callable(
            verify_token
        ):
            raise IdentityUnavailable(
                "qualified session and local token owners required"
            )
        self._authority = authority
        self._verify_token = verify_token
        self._trusted_origin = trusted_origin
        self._clock = clock
        self._requests: dict[int, _BoundRequest] = {}

    def _facts(
        self, state: SessionState, session: Any, verified: VerifiedToken
    ) -> None:
        if not isinstance(state, SessionState):
            raise IdentityUnavailable("qualified EG session evidence unavailable")
        now_ms = int(self._clock() * 1000)
        if state.expires_at_ms <= now_ms:
            raise PermissionError("browser session expired")
        if state.mfa_at_ms is not None and state.mfa_at_ms > now_ms:
            raise PermissionError("browser session MFA timestamp is in the future")
        verified.ensure_current()
        expiry = verified.claims.get("exp")
        if type(expiry) is not int or expiry * 1000 > state.expires_at_ms:
            raise PermissionError("forwarded token outlives source session")
        require_token_binding(verified.claims, state.resolution)
        _require_caller(session, state)

    def _record(self, scope: Any) -> _BoundRequest:
        record = self._requests.get(id(scope))
        if (
            record is None
            or record.scope is not scope
            or record.task is not asyncio.current_task()
        ):
            raise PermissionError("no private authority binding for this request")
        try:
            record.binding.ensure_unchanged(
                scope,
                record.session,
                session_ref=record.session_ref,
                forwarded_token=record.forwarded_token,
            )
        except BaseException:
            if self._requests.get(id(scope)) is record:
                del self._requests[id(scope)]
            raise
        return record

    def invalidate(self, scope: Any) -> None:
        """Trusted composition calls this on refresh; proof must be rebound."""
        record = self._requests.get(id(scope))
        if record is not None and record.scope is scope:
            del self._requests[id(scope)]

    def _require_forwarded_pair(self, scope: Any, forwarded_token: str) -> str:
        require_mutation_proof(scope, trusted_origin=self._trusted_origin)
        credential = session_from_scope(scope)
        if credential is None:
            raise PermissionError("opaque GraphOS session required")
        if type(forwarded_token) is not str or not forwarded_token:
            raise PermissionError("verified local forwarded token required")
        if _headers(scope, b"authorization") != (f"Bearer {forwarded_token}".encode(),):
            raise PermissionError("forwarded credential pairing disagrees")
        return credential

    @asynccontextmanager
    async def _bind_request(
        self,
        scope: Any,
        session: Any,
        *,
        forwarded_token: str,
    ) -> AsyncIterator[None]:
        """Bind normalized trusted forwarding, never client cookie+bearer input.

        E must refuse ambiguous incoming credentials before normalizing. This
        method requires the exact already-forwarded JWT header and a qualified
        caller-session object; it is not an ASGI middleware or login ceremony.
        """
        if id(scope) in self._requests:
            raise PermissionError("request already has a private binding")
        credential = self._require_forwarded_pair(scope, forwarded_token)
        key = secrets.token_bytes(32)
        initial = _snapshot(scope, key)
        state = await self._authority.resolve_session(credential)
        if _snapshot(scope, key) != initial:
            raise PermissionError("request changed during session resolution")
        if not isinstance(state, SessionState):
            raise IdentityUnavailable("qualified EG session evidence unavailable")
        initial_session_ref = state.session_ref
        verified = await self._verify_token(forwarded_token)
        if _snapshot(scope, key) != initial:
            raise PermissionError("request changed during token verification")
        # Token verification may await a remote key source. Its completion is
        # not a session-revocation fence: resolve again AFTER that await.
        state = await self._authority.resolve_session(credential)
        if _snapshot(scope, key) != initial:
            raise PermissionError("request changed during final session resolution")
        self._facts(state, session, verified)
        if state.session_ref != initial_session_ref:
            raise PermissionError("browser session rotated during initial binding")
        if _snapshot(scope, key) != initial:
            raise PermissionError("request changed during caller verification")
        if id(scope) in self._requests:
            raise PermissionError("request acquired another private binding")
        binding = _RequestBinding.capture(
            scope,
            session,
            session_ref=state.session_ref,
            forwarded_token=forwarded_token,
        )
        record = _BoundRequest(
            scope,
            session,
            credential,
            forwarded_token,
            binding,
            state.session_ref,
            asyncio.current_task(),
        )
        self._requests[id(scope)] = record
        try:
            yield
        finally:
            # Do not let an old context manager remove a newer refreshed binding.
            if self._requests.get(id(scope)) is record:
                del self._requests[id(scope)]

    async def verify_request(self, scope: Any) -> Any:
        """Return only the existing WebUI DTO after fresh owner revalidation."""
        record = None
        try:
            record = self._record(scope)
            require_mutation_proof(scope, trusted_origin=self._trusted_origin)
            state = await self._authority.resolve_session(record.credential)
            self._record(scope)
            verified = await self._verify_token(record.forwarded_token)
            self._record(scope)
            # This must be the final awaited owner operation. All remaining
            # snapshot, token-lifetime, caller and rotation checks are synchronous.
            state = await self._authority.resolve_session(record.credential)
            self._record(scope)
            self._facts(state, record.session, verified)
            self._record(scope)
            if state.session_ref != record.session_ref:
                raise PermissionError("browser session rotated")
            try:
                from agent_webui.oidc_session import BrowserSessionEvidence
            except ImportError:
                raise IdentityUnavailable(
                    "reviewed WebUI browser evidence export unavailable"
                ) from None
            return BrowserSessionEvidence(
                request_scope=scope,
                subject=state.resolution.principal_id,
                tenant=state.resolution.request_context["tenant"],
                session_ref=state.session_ref,
                expires_at_ms=state.expires_at_ms,
                mfa_at_ms=state.mfa_at_ms,
            )
        except BaseException:
            if record is not None and self._requests.get(id(scope)) is record:
                del self._requests[id(scope)]
            raise

    async def session_for_request(self, request: Any) -> Any:
        """C's resolver returns the SAME privately bound caller-session instance."""
        await self.verify_request(request.scope)
        return self._record(request.scope).session

    async def before_invocation(self, scope: Any, session: Any) -> None:
        """E's last check rejects substitution after C caller conversion."""
        record = self._record(scope)
        if session is not record.session:
            self.invalidate(scope)
            raise PermissionError("caller session instance was substituted")
        await self.verify_request(scope)
