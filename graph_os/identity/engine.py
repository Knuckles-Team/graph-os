"""The engine-owned identity store, as GraphOS reaches it.

Every identity operation is one ``Method::Identity`` request whose body names
the op family and op (``{"family": "credential", "op": "authenticate",
"request": {...}}``). Two authorities send them:

* the BROKER — GraphOS's own service identity, which holds the exact
  ``identity:authenticate`` scope and submits every op that carries a
  caller-generated secret (sign-in, session touch, one-time tokens, API-key
  verification, second factors);
* the CALLER — a signed-in principal's own verified session, for the
  administrator ops (``identity:admin``) and self-service ops
  (``identity:self``). GraphOS never widens a caller: the engine checks the
  exact scope on the caller's own verified context.

Refusals come back as the engine's typed ``IDENTITY_*`` codes and are raised
as :class:`IdentityRefused`; anything else (transport, engine unavailable) is
:class:`IdentityUnavailable`, so a caller can fail closed without parsing
text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "EngineIdentityPort",
    "IdentityCall",
    "IdentityEngine",
    "IdentityRefused",
    "IdentityReply",
    "IdentityUnavailable",
    "Resolution",
    "SignIn",
]

_REFUSAL_CODE = re.compile(r"\bIDENTITY_[A-Z_]+\b")


class IdentityRefused(Exception):
    """The engine refused an identity op with a typed ``IDENTITY_*`` code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IdentityUnavailable(RuntimeError):
    """The identity store could not be reached or answered out of contract."""


@dataclass(frozen=True)
class IdentityCall:
    """One ``Method::Identity`` op: family, op and its request body."""

    family: str
    op: str
    request: Mapping[str, Any] | None = None

    def wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {"family": self.family, "op": self.op}
        if self.request is not None:
            body["request"] = dict(self.request)
        return body


@dataclass(frozen=True)
class IdentityReply:
    """The engine's ``{"kind": ..., "value": ...}`` answer."""

    kind: str
    value: Any

    def expect(self, kind: str) -> Any:
        if self.kind != kind:
            raise IdentityUnavailable(
                f"identity reply kind {self.kind!r} where {kind!r} was expected"
            )
        return self.value


@dataclass(frozen=True)
class Resolution:
    """A principal's effective authority: what the local issuer puts in a token."""

    principal_id: str
    username: str
    kind: str
    status: str
    is_bootstrap: bool = False
    roles: frozenset[str] = field(default_factory=frozenset)
    groups: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    mfa_required: bool = False
    mfa_enrolled: bool = False
    session_mfa_pending: bool = False
    session_mfa_at_ms: int | None = None

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Resolution:
        mfa_at_ms = value.get("session_mfa_at_ms")
        if mfa_at_ms is not None and (type(mfa_at_ms) is not int or mfa_at_ms < 0):
            raise IdentityUnavailable("identity session MFA time is invalid")
        return cls(
            principal_id=str(value["principal_id"]),
            username=str(value["username"]),
            kind=str(value["kind"]),
            status=str(value["status"]),
            is_bootstrap=bool(value.get("is_bootstrap", False)),
            roles=frozenset(value.get("roles") or ()),
            groups=frozenset(value.get("groups") or ()),
            scopes=frozenset(value.get("scopes") or ()),
            mfa_required=bool(value.get("mfa_required", False)),
            mfa_enrolled=bool(value.get("mfa_enrolled", False)),
            session_mfa_pending=bool(value.get("session_mfa_pending", False)),
            session_mfa_at_ms=mfa_at_ms,
        )

    @property
    def usable(self) -> bool:
        """Active and owing no second factor: may hold a token."""
        return self.status == "active" and not self.session_mfa_pending


@dataclass(frozen=True)
class SignIn:
    """The answer to a sign-in or a second factor (``AuthenticateResult``)."""

    outcome: str
    principal_id: str | None = None
    retry_after_ms: int | None = None

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> SignIn:
        retry = value.get("retry_after_ms")
        return cls(
            outcome=str(value["outcome"]),
            principal_id=value.get("principal_id"),
            retry_after_ms=int(retry) if retry is not None else None,
        )


class IdentityEngine(Protocol):
    """The identity store port. The only way GraphOS reaches identity state."""

    async def broker(self, call: IdentityCall) -> IdentityReply:
        """Send ``call`` as GraphOS's broker identity (``identity:authenticate``)."""
        ...

    async def as_caller(self, session: Any, call: IdentityCall) -> IdentityReply:
        """Send ``call`` under a signed-in principal's own verified session."""
        ...


def parse_reply(payload: Any) -> IdentityReply:
    """Decode the engine's tagged reply, refusing anything off contract."""
    if not isinstance(payload, Mapping) or "kind" not in payload:
        raise IdentityUnavailable("identity reply is not a tagged object")
    return IdentityReply(kind=str(payload["kind"]), value=payload.get("value"))


def refusal_of(error: BaseException) -> Exception:
    """The typed refusal an engine error carries, else ``IdentityUnavailable``."""
    match = _REFUSAL_CODE.search(str(error))
    if match is not None:
        return IdentityRefused(match.group(0))
    return IdentityUnavailable(f"identity store unavailable ({type(error).__name__})")


class EngineIdentityPort:
    """:class:`IdentityEngine` over the generated epistemic-graph client.

    ``client_for`` returns the session-routed client of one graph (the
    process engine's :func:`graph_os.mcp_server.bootstrap.graph_client`);
    ``broker_session`` returns GraphOS's own verified session, re-read on
    every call so a renewed process authority is picked up.
    """

    def __init__(
        self,
        client_for: Callable[[str], Any],
        broker_session: Callable[[], Any],
    ) -> None:
        self._client_for = client_for
        self._broker_session = broker_session

    async def broker(self, call: IdentityCall) -> IdentityReply:
        return await self._send(self._broker_session(), call)

    async def as_caller(self, session: Any, call: IdentityCall) -> IdentityReply:
        return await self._send(session, call)

    async def _send(self, session: Any, call: IdentityCall) -> IdentityReply:
        from epistemic_graph.generated.security import send_identity

        if session is None:
            raise IdentityUnavailable("no verified session for the identity store")
        client = self._client_for(str(session.tenant))
        try:
            with client.use_verified_context(session.engine_verified_context()):
                result = await send_identity(client, {"op": call.wire()})
        except (RuntimeError, ValueError, OSError) as exc:
            raise refusal_of(exc) from exc
        return parse_reply(result.payload)
