"""The external authorities' :class:`~.idp_common.IdentityPort`, over the engine.

The OIDC, SAML, LDAP and SCIM brokers build tagged ``IdentityOp`` mappings and
catch :class:`graph_os.identity.idp_common.IdentityRefused`. These adapters
send those ops through the one engine port (:class:`.engine.IdentityEngine`),
either as GraphOS's broker identity or under a verified caller session (a SCIM
provisioner's own service principal), and translate the engine port's typed
refusals — including an unreachable store — into that one refusal type, so a
background loop such as the LDAP sync logs and retries instead of dying.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any

from .engine import (
    IdentityCall,
    IdentityEngine,
    IdentityRefused,
    IdentityReply,
    IdentityUnavailable,
)
from .idp_common import IdentityRefused as AuthorityRefused

__all__ = ["BrokerPort", "CallerPort"]

_UNAVAILABLE = "IDENTITY_UNAVAILABLE"


def _call_of(op: Mapping[str, Any]) -> IdentityCall:
    request = op.get("request")
    return IdentityCall(
        str(op["family"]), str(op["op"]), request if request is not None else None
    )


async def _translated(pending: Awaitable[IdentityReply]) -> dict[str, Any]:
    """The reply in wire form; every engine-port refusal as the one type."""
    try:
        reply = await pending
    except IdentityRefused as refused:
        raise AuthorityRefused(refused.code) from refused
    except IdentityUnavailable as unavailable:
        raise AuthorityRefused(_UNAVAILABLE) from unavailable
    return {"kind": reply.kind, "value": reply.value}


class BrokerPort:
    """Ops sent as GraphOS's broker identity (``identity:authenticate``)."""

    def __init__(self, engine: IdentityEngine) -> None:
        self._engine = engine

    async def call(self, op: Mapping[str, Any]) -> Mapping[str, Any]:
        return await _translated(self._engine.broker(_call_of(op)))


class CallerPort:
    """Ops sent under one verified caller session (never widened)."""

    def __init__(self, engine: IdentityEngine, session: Any) -> None:
        self._engine = engine
        self._session = session

    async def call(self, op: Mapping[str, Any]) -> Mapping[str, Any]:
        return await _translated(self._engine.as_caller(self._session, _call_of(op)))
