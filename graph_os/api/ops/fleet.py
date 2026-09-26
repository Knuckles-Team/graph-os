"""Governed direct fleet call; dynamic effect comes from gateway_ops."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    HttpShape,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)
from graph_os.fleet.service_child import ServiceChildOutcomeUnknown


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FleetCatalogParams(_Params):
    query: str = Field(default="", max_length=4_096)
    kinds: tuple[str, ...] = ()
    servers: tuple[str, ...] = ()
    browse: bool = False
    cursor: str | None = Field(default=None, max_length=1_024)
    limit: int = Field(default=50, ge=1, le=100)
    context_budget_tokens: int | None = Field(default=None, ge=256, le=1_000_000)


class FleetStatusParams(_Params):
    servers: tuple[str, ...] = ()


class FleetLoadParams(_Params):
    items: tuple[str, ...] = Field(min_length=1, max_length=256)
    auto_unload: bool = False
    evict: str | None = Field(default=None, pattern="^lru$")


class FleetUnloadParams(_Params):
    items: tuple[str, ...] = ()
    servers: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    all_items: bool = False


class FleetCallParams(_Params):
    server: str = Field(min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class FleetResult(BaseModel):
    value: Any


class FleetHealthResult(BaseModel):
    generated_at: float
    sessions: dict[str, Any] | None
    goals: dict[str, Any] | None
    domains: dict[str, dict[str, float]] | None
    dispatch_workers: list[dict[str, Any]] | None
    evidence: dict[str, Any]


class FleetTopologyParams(_Params):
    status: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class FleetTopologyResult(BaseModel):
    generated_at: float
    domains: list[dict[str, Any]] | None
    goals: list[Any] | None
    dispatch_workers: list[dict[str, Any]] | None
    totals: dict[str, int | None]
    page: dict[str, int | None]
    evidence: dict[str, Any]


class FleetContainmentParams(_Params):
    session_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = (
        Field(default=(), max_length=1000)
    )
    domain: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _one_target(self) -> FleetContainmentParams:
        if bool(self.session_ids) == bool(self.domain):
            raise ValueError("provide session_ids or domain")
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("duplicate session target")
        return self


class FleetContainmentResult(BaseModel):
    status: str
    action: str
    affected: list[str]
    applied: dict[str, str]
    count: int


async def handle_fleet_supervision(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Execute tenant-scoped supervisory reads off the serving event loop."""
    from graph_os.gateway.fleet import (
        fleet_health_for_caller,
        fleet_topology_for_caller,
    )

    if op.id == "fleet.health":
        return await asyncio.to_thread(fleet_health_for_caller, context.caller)
    if op.id == "fleet.topology":
        return await asyncio.to_thread(
            fleet_topology_for_caller, context.caller, **params
        )
    raise ValueError("unknown fleet supervisory operation")


async def handle_fleet_containment(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Apply one confirmed containment action with a tenant row fence."""
    from graph_os.gateway.fleet import fleet_set_status_for_caller

    action = op.id.removeprefix("fleet.")
    if action not in {"pause", "kill"}:
        raise ValueError("unknown fleet containment operation")
    return await asyncio.to_thread(
        fleet_set_status_for_caller, context.caller, action=action, **params
    )


async def handle_fleet_operation(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Project catalog and session operations through the one bound gateway."""
    gateway = context.services.get("fleet_gateway")
    if gateway is None:
        raise RuntimeError("fleet gateway is not bound")
    caller = context.caller
    if op.id == "fleet.catalog.search":
        result = await gateway.search(caller, **params)
    elif op.id == "fleet.catalog.list":
        result = await gateway.list(caller, **params)
    elif op.id == "fleet.tools.load":
        result = await gateway.load(caller, **params)
    elif op.id == "fleet.tools.unload":
        result = await gateway.unload(caller, **params)
    elif op.id == "fleet.status":
        result = await gateway.status(caller, **params)
    else:
        raise ValueError("unknown fleet operation")
    return {"value": result}


async def handle_fleet_call(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Require the injected gateway, which rechecks item authority at call time."""
    gateway = context.services.get("fleet_gateway")
    if gateway is None:
        raise RuntimeError("fleet gateway is not bound")
    try:
        result = await gateway.call(
            context.caller,
            params["server"],
            params["tool"],
            params["arguments"],
            expected_effect=op.effect,
            service_identity=context.service_identity,
            owner=context.owner,
            owner_ref=getattr(context, "owner_ref", None),
            fleet_decision=context.fleet_decision,
            registry_digest=context.registry_digest,
        )
    except ServiceChildOutcomeUnknown as exc:
        from graph_os.api.invoke.pipeline import OperationRefused

        raise OperationRefused(
            "INDETERMINATE", {"recovery_ref": exc.recovery_ref}
        ) from exc
    return {"value": result}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="fleet.health",
            verb=Verb.ASK,
            summary="Read tenant-scoped fleet health and dependency evidence",
            examples=("show my tenant's fleet health",),
            params=_Params,
            result=FleetHealthResult,
            binding=Composite(
                handler="graph_os.api.ops.fleet.handle_fleet_supervision"
            ),
            scopes=frozenset({"fleet:read"}),
            surfaces=frozenset({Surface.HTTP}),
            effect=Effect.READ,
        ),
        OpSpec(
            id="fleet.topology",
            verb=Verb.ASK,
            summary="Read a bounded page of tenant fleet sessions",
            examples=("show my tenant's fleet topology",),
            params=FleetTopologyParams,
            result=FleetTopologyResult,
            binding=Composite(
                handler="graph_os.api.ops.fleet.handle_fleet_supervision"
            ),
            scopes=frozenset({"fleet:read"}),
            surfaces=frozenset({Surface.HTTP}),
            effect=Effect.READ,
        ),
        *(
            OpSpec(
                id=f"fleet.{action}",
                verb=Verb.MANAGE,
                summary=f"{action.title()} verified tenant fleet sessions",
                examples=(f"{action} my tenant's fleet sessions",),
                params=FleetContainmentParams,
                result=FleetContainmentResult,
                binding=Composite(
                    handler="graph_os.api.ops.fleet.handle_fleet_containment"
                ),
                scopes=frozenset({"fleet:control"}),
                principals=PrincipalRule.HUMAN_UNDELEGATED,
                surfaces=frozenset({Surface.HTTP, Surface.CONSOLE}),
                effect=Effect.DESTRUCTIVE,
                confirm=Confirm.CONSOLE,
                idempotency=Idempotency.KEY_REQUIRED,
                audit=AuditClass.EVENT,
            )
            for action in ("pause", "kill")
        ),
        OpSpec(
            id="fleet.call",
            verb=Verb.ACT,
            summary="Call an authorized fleet child tool without loading it",
            examples=("call this fleet tool with these arguments",),
            params=FleetCallParams,
            result=FleetResult,
            binding=Composite(handler="graph_os.api.ops.fleet.handle_fleet_call"),
            scopes=frozenset({"mcp:delegate"}),
            http=HttpShape(method="POST", path="/api/v1/fleet/call"),
            effect=Effect.WRITE,
            audit=AuditClass.EVENT,
        ),
        *(
            OpSpec(
                id=f"fleet.catalog.{action}",
                verb=Verb.FIND,
                summary=f"{action.capitalize()} caller-visible fleet catalog items",
                examples=("find authorized fleet tools and skills",),
                params=FleetCatalogParams,
                result=FleetResult,
                binding=Composite(
                    handler="graph_os.api.ops.fleet.handle_fleet_operation"
                ),
                scopes=frozenset({"mcp:discover"}),
                http=(
                    HttpShape(method="GET", path="/api/v1/fleet/catalog")
                    if action == "search"
                    else None
                ),
                effect=Effect.READ,
            )
            for action in ("search", "list")
        ),
        OpSpec(
            id="fleet.tools.load",
            verb=Verb.MANAGE,
            summary="Load authorized fleet items for this MCP session",
            examples=("load these tools for this session",),
            params=FleetLoadParams,
            result=FleetResult,
            binding=Composite(handler="graph_os.api.ops.fleet.handle_fleet_operation"),
            scopes=frozenset({"mcp:delegate"}),
            effect=Effect.WRITE,
            surfaces=frozenset({Surface.MCP}),
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="fleet.tools.unload",
            verb=Verb.MANAGE,
            summary="Unload fleet items from this MCP session",
            examples=("unload my session tools",),
            params=FleetUnloadParams,
            result=FleetResult,
            binding=Composite(handler="graph_os.api.ops.fleet.handle_fleet_operation"),
            scopes=frozenset({"mcp:delegate"}),
            effect=Effect.WRITE,
            surfaces=frozenset({Surface.MCP}),
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="fleet.status",
            verb=Verb.ASK,
            summary="Show child fleet health and this session's loaded items",
            examples=("show my fleet multiplexer status",),
            params=FleetStatusParams,
            result=FleetResult,
            binding=Composite(handler="graph_os.api.ops.fleet.handle_fleet_operation"),
            scopes=frozenset({"mcp:discover"}),
            effect=Effect.READ,
        ),
    )


specs = operations
