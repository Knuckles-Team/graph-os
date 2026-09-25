"""Resident multiplexer cutover and session visibility contract."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog
from graph_os.fleet.multiplexer import (
    SessionVisibilityMiddleware,
    _make_forwarder,
    _register_meta_tools,
    attach_fleet_loader,
)
from graph_os.fleet.multiplexer_ops import MultiplexerOps
from graph_os.fleet.session_loads import SessionLoads
from tests.fleet.catalog_fixture import _NeverRead, multiplexer_from_fixture
from tests.fleet.conftest import fleet_session


class _Ops:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def find_tools(self, caller, **kwargs):
        self.calls.append(("find", caller))
        return {"results": [], "browse": kwargs["browse"]}

    async def load_tools(self, caller, session, items, **kwargs):
        self.calls.append(("load", caller))
        return {"loaded": items, "session": session}

    async def unload_tools(self, caller, session, **kwargs):
        self.calls.append(("unload", caller))
        return {"unloaded": []}

    def multiplexer_status(self, session):
        return {"session": session}

    def dispatchable(self, session, item):
        return session == "a" and item == "s__tool"


@pytest.mark.asyncio
async def test_only_four_resident_fleet_tools_are_registered():
    mcp = FastMCP("test")
    ops = _Ops()
    mux = SimpleNamespace(_multiplexer_ops=ops, _global_visible=set())
    _register_meta_tools(mcp, mux)
    assert {tool.name for tool in await mcp.list_tools()} == {
        "find_tools",
        "load_tools",
        "unload_tools",
        "multiplexer_status",
    }
    assert "list_catalog" not in mux._global_visible
    with fleet_session("mcp:discover"):
        tool = await mcp.get_tool("find_tools")
        result = await tool.fn(browse=True)
    assert result.structured_content["browse"] is True
    assert "mcp:discover" in ops.calls[0][1].effective_scopes


def test_cutover_refuses_missing_governed_ops():
    mcp = FastMCP("test")
    with pytest.raises(RuntimeError, match="governed multiplexer operations"):
        attach_fleet_loader(mcp, catalog_reader=object())


@pytest.mark.asyncio
async def test_factory_mounts_native_tool_with_governed_body(monkeypatch):
    mcp = FastMCP("test")
    monkeypatch.setitem(
        sys.modules,
        "graph_os.fleet.shared_multiplexer",
        SimpleNamespace(
            bind_served_multiplexer=lambda mux: None,
            ServedMultiplexerLoopExtension=lambda mux: object(),
            claim_served_multiplexer_loop=lambda mux: None,
        ),
    )
    monkeypatch.setattr(mcp, "add_extension", lambda extension: None, raising=False)
    captured = {}

    class Ops(_Ops):
        async def read_loaded_item(self, caller, session, item_id, params):
            assert "mcp:delegate" in caller.effective_scopes
            if item_id == "fleet:prompt:s/guide":
                return "Guidance"
            assert item_id == "fleet:resource:s/data://entry"
            return "Record"

    def factory(mux, mount, notify, native_name):
        captured.update(mux=mux, mount=mount, notify=notify, native_name=native_name)
        return Ops()

    mux = attach_fleet_loader(mcp, catalog_reader=_NeverRead(), ops_factory=factory)
    assert captured["mux"] is mux
    calls = []

    async def governed(arguments, caller):
        calls.append((arguments, caller.subject))
        return {"ok": True}

    item = CatalogItem(
        id="s__tool",
        kind="tool",
        name="tool",
        server="s",
        schema={"type": "object", "properties": {"value": {"type": "integer"}}},
    )
    await captured["mount"](item, governed)
    native = captured["native_name"](item)
    assert native in mux._exposed
    with fleet_session("mcp:delegate"):
        tool = await mcp.get_tool("s__tool")
        assert await tool.fn(value=7) == {"ok": True}
    assert calls[0][0] == {"value": 7}

    prompt_item = CatalogItem(
        id="fleet:prompt:s/guide",
        kind="prompt",
        name="guide",
        server="s",
        body="Guidance",
    )
    await captured["mount"](prompt_item, None)
    prompt_name = captured["native_name"](prompt_item)
    assert prompt_name in mux._exposed_items
    assert prompt_name not in mux._exposed
    prompt = await mcp.get_prompt(prompt_name)
    with fleet_session("mcp:delegate"):
        rendered = await prompt.render()
    assert rendered.messages[0].content.text == "Guidance"

    resource_item = CatalogItem(
        id="fleet:resource:s/data://entry",
        kind="resource",
        name="data://entry",
        server="s",
    )
    await captured["mount"](resource_item, None)
    assert captured["native_name"](resource_item) in mux._exposed_items
    resource = await mcp.get_resource(captured["native_name"](resource_item))
    with fleet_session("mcp:delegate"):
        assert await resource.read() == "Record"


def test_loaded_native_tool_is_scoped_to_one_session(tmp_path):
    mux = multiplexer_from_fixture(tmp_path / "empty-catalog.json")
    ops = _Ops()
    mux._multiplexer_ops = ops
    mux._exposed.add("s__tool")
    assert mux.tool_dispatchable("s__tool", session_key="a")
    assert not mux.tool_dispatchable("s__tool", session_key="b")
    assert not mux.tool_dispatchable("unregistered", session_key="a")


def test_native_admission_is_resident_allowlist(tmp_path):
    mux = multiplexer_from_fixture(tmp_path / "empty-catalog.json")
    mcp = FastMCP("test")

    @mcp.tool(name="act")
    def resident() -> str:
        return "ok"

    @mcp.tool(name="legacy_graph_query")
    def legacy() -> str:
        return "unavailable"

    mux.admit_native_tools(mcp)
    assert mux.tool_dispatchable("act")
    assert not mux.tool_dispatchable("legacy_graph_query")


@pytest.mark.asyncio
async def test_loaded_forwarder_calls_governed_invoke(tmp_path, monkeypatch):
    mux = multiplexer_from_fixture(tmp_path / "empty-catalog.json")
    mux._exposed.add("s__tool")
    monkeypatch.setattr("graph_os.fleet.multiplexer._session_key", lambda: "a")
    calls = []

    class Catalog:
        async def get(self, item_id, caller):
            assert item_id == "s__tool"
            assert "mcp:delegate" in caller.effective_scopes
            return SimpleNamespace(kind="tool")

    class Ops(_Ops):
        catalog = Catalog()

        def catalog_id_for_native(self, name):
            return name

        def _forwarder(self, item):
            async def invoke(arguments, caller):
                calls.append((arguments, caller.subject))
                return {"governed": True}

            return invoke

    mux._multiplexer_ops = Ops()
    with fleet_session("mcp:delegate"):
        result = await _make_forwarder(mux, "s__tool")(value=3)
    assert result == {"governed": True}
    assert calls[0][0] == {"value": 3}


@pytest.mark.asyncio
async def test_policy_outage_hides_loaded_native_tool(tmp_path, monkeypatch):
    mux = multiplexer_from_fixture(tmp_path / "empty-catalog.json")
    mux._exposed.add("s__tool")
    monkeypatch.setattr("graph_os.fleet.multiplexer._session_key", lambda: "a")

    class Ops(_Ops):
        async def revoke_invisible(self, caller, key):
            raise RuntimeError("policy unavailable")

        async def redeliver_pending(self, key):
            return False

    mux._multiplexer_ops = Ops()
    mux._global_visible.add("multiplexer_status")
    with fleet_session("mcp:delegate"):
        await SessionVisibilityMiddleware(mux)._refresh_session_policy()
    assert not mux.tool_dispatchable("s__tool", session_key="a")
    assert mux.tool_dispatchable("multiplexer_status", session_key="a")


@pytest.mark.asyncio
async def test_loaded_prompt_body_rechecks_caller_policy():
    item = CatalogItem(
        id="fleet:prompt:s/guide",
        kind="prompt",
        name="guide",
        server="s",
        body="Guidance",
    )
    allowed = [True]

    async def source():
        return (item,)

    async def visible(_item, _caller):
        return allowed[0]

    async def mount(_item, _forwarder):
        return None

    async def notify(_key):
        return True

    async def invoke(*_args):
        raise AssertionError("prompt body is not a fleet tool call")

    ops = MultiplexerOps(
        catalog=FleetCatalog((source,), visible),
        sessions=SessionLoads(),
        loadable=visible,
        mount=mount,
        notify=notify,
        invoke=invoke,
        health=lambda: {},
        native_name=lambda row: "s__guide",
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"})
    )
    result = await ops.load_tools(caller, "a", [item.id])
    assert result["items"][0]["prompt_name"] == "s__guide"
    assert await ops.read_loaded_item(caller, "a", item.id, {}) == "Guidance"
    with pytest.raises(PermissionError, match="not loaded"):
        await ops.read_loaded_item(caller, "b", item.id, {})
    allowed[0] = False
    with pytest.raises(PermissionError, match="unavailable"):
        await ops.read_loaded_item(caller, "a", item.id, {})


@pytest.mark.asyncio
async def test_resource_requires_governed_read_adapter():
    item = CatalogItem(
        id="fleet:resource:s/data://entry",
        kind="resource",
        name="data://entry",
        server="s",
    )

    async def source():
        return (item,)

    async def yes(_item, _caller):
        return True

    async def mount(_item, _forwarder):
        raise AssertionError("resource must not mount without read authority")

    async def notify(_key):
        return True

    async def invoke(*_args):
        raise AssertionError("resource must not use fleet.call tool dispatch")

    ops = MultiplexerOps(
        catalog=FleetCatalog((source,), yes),
        sessions=SessionLoads(),
        loadable=yes,
        mount=mount,
        notify=notify,
        invoke=invoke,
        health=lambda: {},
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"})
    )
    with pytest.raises(RuntimeError, match="governed fleet read adapter"):
        await ops.load_tools(caller, "a", [item.id])
    assert not ops.sessions.loaded("a")
