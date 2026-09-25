"""Caller-authorized graph operations and the node property codec."""

from __future__ import annotations

from typing import Any, Mapping

import msgpack
from pydantic import BaseModel, ConfigDict, Field, RootModel

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


class _NodeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)


class NodeAddParams(_NodeParams):
    properties: dict[str, Any] = Field(default_factory=dict)


class NodeAddResult(RootModel[None]):
    """The legacy node add call returns no value on success."""


class NodePropertiesResult(RootModel[dict[str, Any] | None]):
    """Decoded node properties, or null when the node is absent."""


async def node_add_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> None:
    """Pack a property object and write it to the verified session graph."""
    from graph_os.api.serving import dispatch_public_eg_method

    request = NodeAddParams.model_validate(params)
    await dispatch_public_eg_method(
        EgMethod(service="AddNode", op="AddNode"),
        {
            "node_id": request.node_id,
            "properties_msgpack": msgpack.packb(request.properties, use_bin_type=True),
        },
        context,
    )


async def node_properties_handler(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any] | None:
    """Read and decode a node's properties from the verified session graph."""
    from graph_os.api.serving import dispatch_public_eg_method

    request = _NodeParams.model_validate(params)
    result = await dispatch_public_eg_method(
        EgMethod(service="GetNodeProperties", op="GetNodeProperties"),
        {"node_id": request.node_id},
        context,
    )
    value = getattr(result, "payload", result)
    if isinstance(value, bytes):
        value = msgpack.unpackb(value, raw=False)
    if value is not None and not isinstance(value, dict):
        raise ValueError("EG node properties result is not an object")
    return value


def _graph(name: str, method: str, scope: str, *, write: bool = False) -> OpSpec:
    return OpSpec(
        id=f"graph.{name}",
        verb=Verb.WRITE if write else Verb.ASK,
        summary=f"{'Mutate' if write else 'Read'} graph {name.replace('.', ' ')}.",
        examples=(f"{'Update' if write else 'Show'} graph {name.replace('.', ' ')}",),
        params=EgSchemaRef(
            path=f"contract/schemas/method.request.json#/methods/{method}"
        ),
        result=EgSchemaRef(
            path=f"contract/schemas/result.graph.json#/methods/{method}"
        ),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({scope}),
        effect=Effect.WRITE if write else Effect.READ,
        idempotency=Idempotency.KEY_REQUIRED if write else Idempotency.NATURAL,
        audit=AuditClass.EVENT if write else AuditClass.NONE,
    )


def specs() -> tuple[OpSpec, ...]:
    return (
        _graph("nodes.list", "GetNodesByLabel", "node:read"),
        OpSpec(
            id="graph.nodes.get",
            verb=Verb.ASK,
            summary="Read decoded node properties in the verified graph.",
            examples=("Show this node's properties",),
            params=_NodeParams,
            result=NodePropertiesResult,
            binding=Composite(handler="graph_os.api.ops.graph.node_properties_handler"),
            scopes=frozenset({"node:read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="graph.nodes.add",
            verb=Verb.WRITE,
            summary="Add a node with an object of properties in the verified graph.",
            examples=("Add this node with its properties",),
            params=NodeAddParams,
            result=NodeAddResult,
            binding=Composite(handler="graph_os.api.ops.graph.node_add_handler"),
            scopes=frozenset({"node:write"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        _graph("nodes.remove", "RemoveNode", "node:write", write=True),
        _graph("edges.list", "GetEdgesPage", "edge:read"),
        _graph("edges.get", "GetEdgeProperties", "edge:read"),
        _graph("edges.add", "AddEdge", "edge:write", write=True),
        _graph("edges.remove", "RemoveEdge", "edge:write", write=True),
    )
