"""Security posture read operation (GRAPHOS-OPS-R024.2).

Implements the security portion of GRAPHOS-OPS-R024: a single
``security.posture.read`` operation surfacing the current security posture
to an authorized caller through one typed port, matching the ingest-runner
facade convention in :mod:`graph_os.api.ops.ingest`. An uncomposed reader
fails closed with a typed ``UNAVAILABLE`` refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import Composite, Effect, OpSpec, Verb


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecurityPostureReadParams(_Params):
    pass


class SecurityPosture(BaseModel):
    """A tenant-scoped security posture; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    findings: tuple[str, ...] = ()


class SecurityPostureReadResult(_Params):
    value: dict[str, Any]


@runtime_checkable
class SecurityPostureReader(Protocol):
    """The one typed port GraphOS calls the security posture method through."""

    async def read(self, *, tenant: str) -> SecurityPosture: ...


def _bound_reader(context: Any) -> SecurityPostureReader:
    reader = context.services.get("security_posture_reader")
    if reader is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "security posture reader is not composed"}
        )
    return reader


async def handle_security_posture_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's security posture through the composed reader."""
    reader = _bound_reader(context)
    posture = await reader.read(tenant=context.caller.tenant)
    return {"value": posture.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="security.posture.read",
            verb=Verb.ASK,
            summary="Show this tenant's current security posture",
            examples=("show the current security posture",),
            params=SecurityPostureReadParams,
            result=SecurityPostureReadResult,
            binding=Composite(
                handler="graph_os.api.ops.security.handle_security_posture_read"
            ),
            scopes=frozenset({"security:read"}),
            effect=Effect.READ,
        ),
    )


specs = operations

__all__ = [
    "SecurityPosture",
    "SecurityPostureReadParams",
    "SecurityPostureReadResult",
    "SecurityPostureReader",
    "handle_security_posture_read",
    "operations",
    "specs",
]
