"""Explicit unavailable adapters until canonical owner bindings exist."""

from .engine import IdentityUnavailable
from .ports import CredentialState, SessionState


class UnavailableIdentityAuthority:
    """Never fabricate missing source expiry or live session evidence."""

    async def resolve_credential(self, credential: str) -> CredentialState:
        raise IdentityUnavailable("qualified EG credential expiry binding unavailable")

    async def resolve_session(self, credential: str) -> SessionState:
        raise IdentityUnavailable("qualified EG session evidence binding unavailable")
