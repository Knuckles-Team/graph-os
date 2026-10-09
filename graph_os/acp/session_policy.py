"""Typed ACP session-policy model and admission check (GRAPHOS-ACP-001, R001.1).

Covers only GraphOS's session-admission and policy partition of the
conversational ACP gateway: admitting a session for a verified principal,
binding it immutably, enforcing idle/absolute expiry, and refusing while the
agent-utilities chat adapter is unavailable. The chat adapter's own behavior
and the terminal client's rendering are each owned elsewhere; see
``specs/conversational-acp-gateway/spec.md``.

No transport is wired in this slice; a later child requirement connects this
admission check to the actual ACP endpoint.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class AcpRefusalReason(enum.Enum):
    """Exact, typed reasons an ACP session is refused."""

    UNAUTHENTICATED = "unauthenticated"
    IDLE_EXPIRED = "idle_expired"
    ABSOLUTE_EXPIRED = "absolute_expired"
    CHAT_ADAPTER_UNAVAILABLE = "chat_adapter_unavailable"


class AcpRefusal(Exception):
    """Raised instead of admitting a session or accepting a message."""

    def __init__(self, reason: AcpRefusalReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class PrincipalContext:
    """The verified identity snapshot bound to an ACP session at admission.

    Produced by the existing GraphOS identity path (GRAPHOS-IDENTITY-001);
    this model does not itself verify a credential.
    """

    principal_id: str
    tenant: str
    scopes: frozenset[str]

    def __post_init__(self) -> None:
        if not self.principal_id or not self.tenant:
            raise ValueError("principal_id and tenant are required")


@dataclass(frozen=True)
class AcpSessionPolicy:
    """Configured idle and absolute limits for an ACP session."""

    idle_limit: timedelta
    absolute_limit: timedelta

    def __post_init__(self) -> None:
        if self.idle_limit <= timedelta(0) or self.absolute_limit <= timedelta(0):
            raise ValueError("idle_limit and absolute_limit must be positive")


@dataclass(frozen=True)
class AcpSessionSnapshot:
    """An admitted session's immutable binding and expiry bookkeeping.

    ``last_activity_at`` is the only field a successful ``touch`` may advance;
    every other field is fixed at admission for the session's whole life.
    """

    principal: PrincipalContext
    admitted_at: datetime
    absolute_expires_at: datetime
    last_activity_at: datetime

    def touched(self, now: datetime) -> AcpSessionSnapshot:
        return AcpSessionSnapshot(
            principal=self.principal,
            admitted_at=self.admitted_at,
            absolute_expires_at=self.absolute_expires_at,
            last_activity_at=now,
        )


class AcpSessionAdmission:
    """Admits and re-checks ACP sessions against identity and policy state.

    ``chat_adapter_available`` is a caller-supplied probe of agent-utilities'
    chat adapter reachability; GraphOS consults it before admitting a session
    and before accepting any message, and never substitutes a local reply or
    a REST fallback when it reports unavailable.
    """

    def __init__(self, policy: AcpSessionPolicy) -> None:
        self._policy = policy

    def admit(
        self,
        principal: PrincipalContext | None,
        *,
        chat_adapter_available: bool,
        now: datetime | None = None,
    ) -> AcpSessionSnapshot:
        if not chat_adapter_available:
            raise AcpRefusal(AcpRefusalReason.CHAT_ADAPTER_UNAVAILABLE)
        if principal is None:
            raise AcpRefusal(AcpRefusalReason.UNAUTHENTICATED)
        moment = now or datetime.now(UTC)
        return AcpSessionSnapshot(
            principal=principal,
            admitted_at=moment,
            absolute_expires_at=moment + self._policy.absolute_limit,
            last_activity_at=moment,
        )

    def check_message(
        self,
        session: AcpSessionSnapshot,
        *,
        chat_adapter_available: bool,
        now: datetime | None = None,
    ) -> AcpSessionSnapshot:
        if not chat_adapter_available:
            raise AcpRefusal(AcpRefusalReason.CHAT_ADAPTER_UNAVAILABLE)
        moment = now or datetime.now(UTC)
        if moment >= session.absolute_expires_at:
            raise AcpRefusal(AcpRefusalReason.ABSOLUTE_EXPIRED)
        if moment - session.last_activity_at >= self._policy.idle_limit:
            raise AcpRefusal(AcpRefusalReason.IDLE_EXPIRED)
        return session.touched(moment)
