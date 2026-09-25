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
    Verb,
)


class FleetCallParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str = Field(min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class FleetCallResult(BaseModel):
    value: Any


async def handle_fleet_call(
    context: Any, params: Mapping[str, Any], _op: OpSpec
) -> dict[str, Any]:
    """Require the injected gateway, which rechecks item authority at call time."""
    gateway = context.services.get("fleet_gateway")
    if gateway is None:
        raise RuntimeError("fleet gateway is not bound")
    result = await gateway.call(
        context.caller, params["server"], params["tool"], params["arguments"]
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
            result=FleetCallResult,
            binding=Composite(handler="graph_os.api.ops.fleet.handle_fleet_call"),
            scopes=frozenset({"mcp:delegate"}),
            effect=Effect.WRITE,
            audit=AuditClass.EVENT,
        ),
    )
