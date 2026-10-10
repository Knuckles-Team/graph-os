"""Identity composition root: build the concrete runtime once, expose only ports.

Dependents receive ``CredentialAuthority`` and ``SessionAuthority`` objects and
never import a concrete implementation. Absent authoritative facts or a
malformed authority answer fail closed with ``IdentityUnavailable``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .engine import IdentityUnavailable
from .ports import (
    CredentialAuthority,
    CredentialState,
    SessionAuthority,
    SessionState,
)


@dataclass(frozen=True, slots=True)
class IdentityPorts:
    """The only identity surface handed to dependents."""

    credentials: CredentialAuthority
    sessions: SessionAuthority


class _CheckedRuntime:
    """Port-typed view over the single concrete runtime; validates answers."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def resolve_credential(self, credential: str) -> CredentialState:
        state = await self._runtime.resolve_credential(credential)
        if not isinstance(state, CredentialState):
            raise IdentityUnavailable("credential authority returned no state")
        return state

    async def resolve_session(self, credential: str) -> SessionState:
        state = await self._runtime.resolve_session(credential)
        if not isinstance(state, SessionState):
            raise IdentityUnavailable("session authority returned no state")
        return state


def build_identity_ports(
    credentials: Any,
    sessions: Any,
    *,
    runtime_factory: Callable[[Any, Any], Any],
) -> IdentityPorts:
    """Construct the concrete runtime exactly once and return port-typed views."""
    if credentials is None or sessions is None:
        raise IdentityUnavailable("authoritative identity sources required")
    runtime = _CheckedRuntime(runtime_factory(credentials, sessions))
    return IdentityPorts(credentials=runtime, sessions=runtime)
