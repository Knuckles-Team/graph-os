"""Parallel edge property parity over the verified graph target."""

from types import SimpleNamespace
from typing import Any

import msgpack
import pytest
from pydantic import ValidationError

from graph_os.api.ops import graph
from graph_os.api.registry import Composite, Effect, Executor


class EdgeClient:
    def __init__(self) -> None:
        self.properties: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def invoke_method(
        self, method: str, params: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((method, params, kwargs))
        key = (params["source_id"], params["target_id"])
        if method == "AddEdge":
            self.properties.setdefault(key, []).append(
                msgpack.unpackb(params["properties_msgpack"], raw=False)
            )
            return "ok"
        if method == "GetEdgeProperties":
            values = self.properties.get(key)
            return msgpack.packb(values, use_bin_type=True) if values else None
        raise AssertionError(method)


def context(client: EdgeClient, *, graph_name: str = "tenant-a/research") -> Any:
    session = SimpleNamespace(
        graph=graph_name,
        tenant="tenant-a",
        actor=SimpleNamespace(actor_id="user:1"),
        ensure_authority_current=lambda: None,
    )
    caller = SimpleNamespace(session=session, tenant="tenant-a", principal="user:1")
    return SimpleNamespace(client=client, caller=caller, idempotency_key="request:1")


@pytest.mark.asyncio
async def test_edge_add_and_properties_round_trip_parallel_msgpack() -> None:
    specs = {op.id: op for op in graph.specs()}
    add = specs["graph.edges.add"]
    get = specs["graph.edges.get"]
    assert isinstance(add.binding, Composite)
    assert isinstance(get.binding, Composite)
    assert add.executor is Executor.CALLER
    assert get.executor is Executor.CALLER
    assert add.effect is Effect.WRITE
    assert add.scopes == frozenset({"edge:write"})
    assert get.scopes == frozenset({"edge:read"})

    client = EdgeClient()
    ctx = context(client)
    pair = {"source_id": "node:a", "target_id": "node:b"}
    first = {"relation": "knows", "weight": 2}
    second = {"relation": "supports", "nested": {"active": True}}
    assert await graph.edge_add_handler(ctx, {**pair, "properties": first}, add) is None
    assert await graph.edge_add_handler(ctx, {**pair, "properties": second}, add) is None
    assert await graph.edge_properties_handler(ctx, pair, get) == [first, second]
    assert await graph.edge_properties_handler(
        ctx, {"source_id": "node:b", "target_id": "node:a"}, get
    ) == []
    assert {call[2]["graph"] for call in client.calls} == {"tenant-a/research"}
    assert client.calls[0][2]["idempotency_key"] == "request:1"

    assert await graph.edge_add_handler(ctx, pair, add) is None
    assert client.properties[("node:a", "node:b")][-1] == {}


@pytest.mark.asyncio
async def test_edge_adapter_rejects_retargeting_and_unverified_session() -> None:
    client = EdgeClient()
    ctx = context(client)
    specs = {op.id: op for op in graph.specs()}
    pair = {"source_id": "node:a", "target_id": "node:b"}
    with pytest.raises(ValidationError):
        await graph.edge_add_handler(
            ctx, {**pair, "properties": {}, "graph": "other"},
            specs["graph.edges.add"],
        )
    ctx.caller.session.tenant = "other"
    with pytest.raises(PermissionError, match="graph target"):
        await graph.edge_properties_handler(ctx, pair, specs["graph.edges.get"])
    assert client.calls == []


@pytest.mark.asyncio
async def test_edge_properties_accepts_json_and_rejects_non_object_rows() -> None:
    class JsonClient(EdgeClient):
        async def invoke_method(
            self, method: str, params: dict[str, Any], **kwargs: Any
        ) -> Any:
            return [{"relation": "knows"}] if params["source_id"] == "ok" else [1]

    client = JsonClient()
    get = next(op for op in graph.specs() if op.id == "graph.edges.get")
    assert await graph.edge_properties_handler(
        context(client), {"source_id": "ok", "target_id": "b"}, get
    ) == [{"relation": "knows"}]
    with pytest.raises(ValueError, match="list of objects"):
        await graph.edge_properties_handler(
            context(client), {"source_id": "bad", "target_id": "b"}, get
        )
