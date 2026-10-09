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

from graph_os.api.ops._common import build_tenant_read_op, handle_tenant_read, read_op_models
from graph_os.api.registry import OpSpec

SecurityPostureReadParams, SecurityPostureReadResult = read_op_models("SecurityPostureRead")


class SecurityPosture(BaseModel):
    """A tenant-scoped security posture; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    findings: tuple[str, ...] = ()


@runtime_checkable
class SecurityPostureReader(Protocol):
    """The one typed port GraphOS calls the security posture method through."""

    async def read(self, *, tenant: str) -> SecurityPosture: ...


async def handle_security_posture_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's security posture through the composed reader."""
    return await handle_tenant_read(
        context,
        service_name="security_posture_reader",
        unavailable_reason="security posture reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        build_tenant_read_op(
            op_id="security.posture.read",
            summary="Show this tenant's current security posture",
            examples=("show the current security posture",),
            params=SecurityPostureReadParams,
            result=SecurityPostureReadResult,
            handler="graph_os.api.ops.security.handle_security_posture_read",
            scope="security:read",
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
