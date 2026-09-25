"""Governed direct fleet call; dynamic effect comes from gateway_ops."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    OpSpec,
    Surface,
    Verb,
)


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
    )
    return {"value": result}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="fleet.call",
            verb=Verb.ACT,
            summary="Call an authorized fleet child tool without loading it",
            examples=("call this fleet tool with these arguments",),
            params=FleetCallParams,
            result=FleetResult,
            binding=Composite(handler="graph_os.api.ops.fleet.handle_fleet_call"),
            scopes=frozenset({"mcp:delegate"}),
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
