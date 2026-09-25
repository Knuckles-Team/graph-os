"""Node property codec parity over a verified graph target."""

from types import SimpleNamespace
from typing import Any

import msgpack
import pytest
from pydantic import ValidationError

from graph_os.api.ops import graph
from graph_os.api.registry import Composite, Effect, Executor


class NodeClient:
    def __init__(self) -> None:
        self.properties: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def invoke_method(
        self, method: str, params: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((method, params, kwargs))
        if method == "AddNode":
            self.properties[params["node_id"]] = params["properties_msgpack"]
            return "ok"
        if method == "GetNodeProperties":
            return self.properties.get(params["node_id"])
        raise AssertionError(method)


def context(client: NodeClient, *, graph_name: str = "tenant-a/research") -> Any:
    session = SimpleNamespace(
        graph=graph_name,
        tenant="tenant-a",
        actor=SimpleNamespace(actor_id="user:1"),
        ensure_authority_current=lambda: None,
    )
    caller = SimpleNamespace(session=session, tenant="tenant-a", principal="user:1")
    return SimpleNamespace(client=client, caller=caller, idempotency_key="request:1")


@pytest.mark.asyncio
async def test_node_add_and_properties_round_trip_legacy_msgpack() -> None:
    specs = {op.id: op for op in graph.specs()}
    add = specs["graph.nodes.add"]
    get = specs["graph.nodes.get"]
    assert isinstance(add.binding, Composite)
    assert isinstance(get.binding, Composite)
    assert add.executor is Executor.CALLER
    assert get.executor is Executor.CALLER
    assert add.effect is Effect.WRITE
    assert add.scopes == frozenset({"node:write"})
    assert get.scopes == frozenset({"node:read"})

    client = NodeClient()
    ctx = context(client)
    properties = {"label": "café", "count": 2, "nested": {"enabled": True}}
    assert await graph.node_add_handler(
        ctx, {"node_id": "node:1", "properties": properties}, add
    ) is None
    assert msgpack.unpackb(client.properties["node:1"], raw=False) == properties
    assert (
        await graph.node_properties_handler(ctx, {"node_id": "node:1"}, get)
        == properties
    )
    assert await graph.node_properties_handler(ctx, {"node_id": "missing"}, get) is None
    assert [call[2]["graph"] for call in client.calls] == [
        "tenant-a/research",
        "tenant-a/research",
        "tenant-a/research",
    ]
    assert client.calls[0][2]["idempotency_key"] == "request:1"

    assert await graph.node_add_handler(ctx, {"node_id": "empty"}, add) is None
    assert msgpack.unpackb(client.properties["empty"], raw=False) == {}


@pytest.mark.asyncio
async def test_node_adapter_rejects_retargeting_and_unverified_session() -> None:
    client = NodeClient()
    ctx = context(client)
    specs = {op.id: op for op in graph.specs()}
    with pytest.raises(ValidationError):
        await graph.node_add_handler(
            ctx, {"node_id": "node:1", "properties": {}, "graph": "other"},
            specs["graph.nodes.add"],
        )
    ctx.caller.session.tenant = "other"
    with pytest.raises(PermissionError, match="graph target"):
        await graph.node_properties_handler(
            ctx, {"node_id": "node:1"}, specs["graph.nodes.get"]
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_node_properties_rejects_non_object_engine_payload() -> None:
    client = NodeClient()
    client.properties["bad"] = msgpack.packb([1, 2], use_bin_type=True)
    get = next(op for op in graph.specs() if op.id == "graph.nodes.get")
    with pytest.raises(ValueError, match="not an object"):
        await graph.node_properties_handler(context(client), {"node_id": "bad"}, get)
