"""Security posture read operation (GRAPHOS-OPS-R024.2).

``security.posture.read`` surfaces whatever the composed
``security_posture_reader`` port currently reports for the caller's own
tenant. :mod:`graph_os.api.ops._common` supplies the OpSpec, the
not-composed refusal, and the empty-params/result pair; this module adds
only the port protocol and the findings shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from graph_os.api.ops import _common
from graph_os.api.registry import OpSpec

SecurityPostureReadParams, SecurityPostureReadResult = _common.read_op_models(
    "SecurityPostureRead"
)
#: A tenant-scoped security posture; the reader owns its exact shape.
SecurityPosture = _common.tenant_state_model(
    "SecurityPosture", field_name="findings", field_type=tuple[str, ...], default=()
)


@runtime_checkable
class SecurityPostureReader(Protocol):
    """The one typed port GraphOS calls the security posture method through."""

    async def read(self, *, tenant: str) -> SecurityPosture: ...


async def handle_security_posture_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's security posture through the composed reader."""
    return await _common.handle_tenant_read(
        context,
        service_name="security_posture_reader",
        unavailable_reason="security posture reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        _common.build_tenant_read_op(
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
