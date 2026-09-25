"""Authorize export of the served EG context endpoint to AU workers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_utilities.layers.contracts import McpEndpoint
from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.harness_context import (
    ContextEndpointUnavailable,
    context_endpoint_payload,
)
from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import (
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)


class ContextEndpointParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint_url: str = Field(min_length=1)
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tools: list[str]
    operations: list[str]


class ContextEndpointResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: McpEndpoint
    proof: ContextProof


async def context_endpoint_handler(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return only a secret reference after a live authorized MCP probe."""

    if op.id != "harness.context_endpoint" or params:
        raise OperationRefused("INVALID_ARGUMENT")
    export = context.services.get("context_endpoint_export")
    if not callable(export):
        raise OperationRefused("UNAVAILABLE")
    try:
        endpoint, proof = await export()
        payload = context_endpoint_payload(endpoint, proof)
        return ContextEndpointResult.model_validate(payload).model_dump(mode="json")
    except ContextEndpointUnavailable as exc:
        raise OperationRefused("UNAVAILABLE") from exc


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="harness.context_endpoint",
            verb=Verb.MANAGE,
            summary="Get the verified EG MCP context endpoint for an AU worker.",
            examples=("Give this authorized AU worker its context endpoint",),
            params=ContextEndpointParams,
            result=ContextEndpointResult,
            binding=Composite(
                handler="graph_os.api.ops.harness.context_endpoint_handler"
            ),
            scopes=frozenset({"mcp:discover", "mcp:delegate"}),
            effect=Effect.READ,
            principals=PrincipalRule.SERVICE_ONLY,
            surfaces=frozenset({Surface.HTTP, Surface.MCP}),
            idempotency=Idempotency.NATURAL,
        ),
    )
