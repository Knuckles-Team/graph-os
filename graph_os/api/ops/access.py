"""Access and approval operations for the GraphOS intent registry (MCPI-11)."""

from __future__ import annotations

from typing import Any, Literal

from agent_utilities.security.elevation import (
    ElevationApproval,
    ElevationRevocation,
    ElevationScope,
    ElevationView,
)
from pydantic import BaseModel, ConfigDict, Field, RootModel

from graph_os.access.service import (
    approve_elevation,
    check_access,
    explain_policy,
    get_lease,
    list_elevations,
    list_leases,
    request_elevation,
    revoke_elevation,
)
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)

#: Re-exported so ``registry_factory.get_registry()`` can resolve each
#: ``Composite(handler="graph_os.api.ops.access.<name>")`` binding from this
#: module's own namespace, matching the ``agents``/``browser``/``fleet``
#: ops-module convention even though the implementations live in the
#: service layer (``graph_os.access.service``).
__all__ = [
    "operations",
    "approve_elevation",
    "check_access",
    "explain_policy",
    "get_lease",
    "list_elevations",
    "list_leases",
    "request_elevation",
    "revoke_elevation",
]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(_Params):
    pass


class ElevationAsk(_Params):
    elevation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    scopes: list[ElevationScope] = Field(min_length=1, max_length=16)
    span_ms: int = Field(gt=0, le=86_400_000)
    justification: str = Field(min_length=1, max_length=2048)


class ElevationPage(_Params):
    elevations: list[ElevationView]


class LeaseList(_Params):
    kind: str | None = None
    status: Literal["active", "consumed", "revoked", "expired"] | None = None
    cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


class LeaseGet(_Params):
    lease_id: str = Field(min_length=1, max_length=256)


class AccessCheck(_Params):
    graph: str = Field(min_length=1, max_length=512)
    agent_id: str = Field(min_length=1, max_length=256)
    action: Literal["read", "write"] = "read"


class AccessDecision(_Params):
    allowed: bool


class PolicyExplain(_Params):
    plan: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class JsonResult(RootModel[dict[str, Any]]):
    pass


_ALL = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A})
_CONSOLE = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A, Surface.CONSOLE})


def _op(
    name: str,
    verb: Verb,
    summary: str,
    params: type[BaseModel],
    result: type[BaseModel],
    *,
    scopes: tuple[str, ...],
    effect: Effect = Effect.READ,
    principals: PrincipalRule = PrincipalRule.ANY,
    confirm: Confirm | None = None,
    surfaces: frozenset[Surface] = _ALL,
    idempotency: Idempotency = Idempotency.NONE,
) -> OpSpec:
    handler = {
        "access.elevation.request": "request_elevation",
        "access.elevation.list": "list_elevations",
        "access.elevation.revoke": "revoke_elevation",
        "access.elevation.approve": "approve_elevation",
        "access.leases.list": "list_leases",
        "access.leases.get": "get_lease",
        "access.check": "check_access",
        "access.explain_policy": "explain_policy",
    }[name]
    return OpSpec(
        id=name,
        verb=verb,
        summary=summary,
        examples=(summary,),
        params=params,
        result=result,
        binding=Composite(handler=f"graph_os.api.ops.access.{handler}"),
        scopes=frozenset(scopes),
        effect=effect,
        principals=principals,
        confirm=confirm,
        surfaces=surfaces,
        idempotency=idempotency,
        audit=AuditClass.EVENT if effect is not Effect.READ else AuditClass.NONE,
    )


def operations() -> tuple[OpSpec, ...]:
    """Supported access operations; native EG deny is tracked as a gap.

    ``approvals.list``/``get``/``grant``/``deny`` are implemented in
    :mod:`graph_os.access.service` but intentionally not registered here:
    the installed EG contract does not yet publish the ``approvals:read``/
    ``approvals:decide`` scopes they require (GRAPHOS-IDENTITY-R019 follow-up,
    tracked in ``specs/identity-access/tasks.md``).
    """
    return (
        _op(
            "access.elevation.request",
            Verb.MANAGE,
            "Request a time-boxed elevation",
            ElevationAsk,
            ElevationView,
            scopes=("rbac:elevation",),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
        ),
        _op(
            "access.elevation.list",
            Verb.ASK,
            "List visible elevation requests",
            Empty,
            ElevationPage,
            scopes=("rbac:elevation-read",),
        ),
        _op(
            "access.elevation.revoke",
            Verb.MANAGE,
            "Revoke an elevation",
            ElevationRevocation,
            ElevationView,
            scopes=("rbac:elevation",),
            effect=Effect.DESTRUCTIVE,
        ),
        _op(
            "access.elevation.approve",
            Verb.MANAGE,
            "Approve an exact elevation request",
            ElevationApproval,
            ElevationView,
            scopes=("rbac:approve-elevation",),
            effect=Effect.ADMIN,
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            confirm=Confirm.CONSOLE,
            surfaces=_CONSOLE,
        ),
        _op(
            "access.leases.list",
            Verb.ASK,
            "List tenant control leases",
            LeaseList,
            JsonResult,
            scopes=("lease:read",),
        ),
        _op(
            "access.leases.get",
            Verb.ASK,
            "Get a tenant control lease",
            LeaseGet,
            JsonResult,
            scopes=("lease:read",),
        ),
        _op(
            "access.check",
            Verb.WHY,
            "Check current graph access",
            AccessCheck,
            AccessDecision,
            scopes=("security:check",),
        ),
        _op(
            "access.explain_policy",
            Verb.WHY,
            "Explain RLS policy for a query plan",
            PolicyExplain,
            JsonResult,
            scopes=("explain:read",),
        ),
    )
