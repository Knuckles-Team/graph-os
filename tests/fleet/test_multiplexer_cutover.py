"""Resident multiplexer cutover and session visibility contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from graph_os.fleet.multiplexer import (
    SessionVisibilityMiddleware,
    _make_forwarder,
    _register_meta_tools,
    attach_fleet_loader,
)
from tests.fleet.catalog_fixture import multiplexer_from_fixture
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
