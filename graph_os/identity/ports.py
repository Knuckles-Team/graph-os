"""Injected owner ports; these are not generated EG wire response schemas.

The public EG resolution currently lacks source expiry and browser session
facts. Composition must supply them from qualified authority or remain
unavailable. No port is selected from request headers or ASGI state.
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .engine import IdentityUnavailable, Resolution


@dataclass(frozen=True, slots=True, repr=False)
class CredentialState:
    """Exact credential's current owner response, supplied by an injected port."""

    resolution: Resolution
    expires_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.resolution, Resolution):
            raise IdentityUnavailable("qualified credential resolution required")
        if type(self.expires_at_ms) is not int or self.expires_at_ms < 0:
            raise IdentityUnavailable("authoritative credential expiry required")


@dataclass(frozen=True, slots=True, repr=False)
class SessionState(CredentialState):
    """Backend composition facts, never a parallel BrowserSessionEvidence DTO.

    The session reference must be the actual authority binding reference, not
    a SessionView display handle. This class cannot qualify its provenance.
    All fields are explicit, including nullable MFA; there are no defaults.
    """

    session_ref: str
    mfa_at_ms: int | None

    def __post_init__(self) -> None:
        CredentialState.__post_init__(self)
        if (
            type(self.session_ref) is not str
            or not self.session_ref
            or self.session_ref != self.session_ref.strip()
            or re.fullmatch(r"[0-9a-fA-F]{12}", self.session_ref)
        ):
            raise IdentityUnavailable(
                "authoritative session binding reference required"
            )
        if self.mfa_at_ms is not None and (
            type(self.mfa_at_ms) is not int or self.mfa_at_ms < 0
        ):
            raise IdentityUnavailable("authoritative session MFA timestamp required")


class CredentialAuthority(Protocol):
    async def resolve_credential(self, credential: str) -> CredentialState:
        """Verify the exact credential and return current policy and source expiry."""
        ...


class SessionAuthority(Protocol):
    async def resolve_session(self, credential: str) -> SessionState:
        """Verify current session including revocation, rotation and expiry."""
        ...


class SigningKeyStore(Protocol):
    def get(self, name: str) -> str | None:
        """Read an existing secret-backed issuer key ring; never create one."""
        ...


class VerifiedToken(Protocol):
    claims: Mapping[str, Any]

    def ensure_current(self) -> None: ...


VerifyToken = Callable[[str], Awaitable[VerifiedToken]]
