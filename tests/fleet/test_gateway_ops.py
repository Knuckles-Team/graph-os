"""Focused contract for the new dynamic multiplexer operation core."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog, connector_items
from graph_os.fleet.multiplexer_ops import MultiplexerOps
from graph_os.fleet.session_loads import LoadCapExceeded, SessionLoads


@pytest.mark.asyncio
async def test_catalog_is_one_filtered_view_across_kinds() -> None:
    rows = (
        CatalogItem(
            "fleet:tool:s/read",
            "tool",
            "read",
            server="s",
            required_scopes=frozenset({"data:read"}),
        ),
        CatalogItem("fleet:prompt:s/guide", "prompt", "guide", server="s"),
        CatalogItem("fleet:skill:s/review", "skill", "review", server="s"),
        *connector_items([{"pack": "p", "name": "sync", "op": "ingest.sources.sync"}]),
    )

    async def source():
        return rows

    async def visible(item, _caller):
        return item.name != "guide"

    catalog = FleetCatalog([source], visible)
    caller = SimpleNamespace(effective_scopes=frozenset({"mcp:discover"}))
    result = await catalog.search(caller, browse=True)
    assert [item["name"] for item in result["items"]] == ["sync", "review"]
    assert await catalog.get("fleet:tool:s/read", caller) is None
    assert await catalog.get("fleet:prompt:s/guide", caller) is None


@pytest.mark.asyncio
async def test_load_cap_lru_and_forwarder_invokes_governed_fleet_call() -> None:
    clock = [10.0]
    sessions = SessionLoads(cap=1, idle_ttl_seconds=60, clock=lambda: clock[0])
    rows = (
        CatalogItem(
            "fleet:tool:s/read", "tool", "read", server="s", schema={"type": "object"}
        ),
        CatalogItem("fleet:tool:s/write", "tool", "write", server="s"),
    )

    async def source():
        return rows

    async def yes(_item, _caller):
        return True

    mounted = {}
    calls = []
    notifications = []

    async def mount(item, forwarder):
        mounted[item.id] = forwarder

    async def notify(session_key):
        notifications.append(session_key)
        return True

    async def invoke(op, params, caller, surface):
        calls.append((op, params, caller, surface))
        return {"ok": True}

    ops = MultiplexerOps(
        catalog=FleetCatalog([source], yes),
        sessions=sessions,
        loadable=yes,
        mount=mount,
        notify=notify,
        invoke=invoke,
        health=lambda: {"s": "healthy"},
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"})
    )
    first = await ops.load_tools(caller, "a", [rows[0].id])
    assert first["items"][0]["fallback"]["op"] == "fleet.call"
    assert ops.dispatchable("a", rows[0].id)
    assert not ops.dispatchable("b", rows[0].id)
    assert await mounted[rows[0].id]({"x": 1}, caller) == {"ok": True}
    assert calls == [
        (
            "fleet.call",
            {"server": "s", "tool": "read", "arguments": {"x": 1}},
            caller,
            "mcp",
        )
    ]
    with pytest.raises(LoadCapExceeded):
        await ops.load_tools(caller, "a", [rows[1].id])
    clock[0] = 20.0
    second = await ops.load_tools(caller, "a", [rows[1].id], evict="lru")
    assert second["evicted"] == [rows[0].id]
    assert ops.dispatchable("a", rows[1].id)
    assert ops.multiplexer_status("a")["session"]["used"] == 1
    assert notifications == ["a", "a"]
    clock[0] = 81.0
    assert sessions.loaded("a") == frozenset()


@pytest.mark.asyncio
async def test_load_is_fail_closed_on_rejected_item() -> None:
    item = CatalogItem("fleet:tool:s/admin", "tool", "admin", server="s")

    async def source():
        return (item,)

    async def yes(_item, _caller):
        return True

    async def no(_item, _caller):
        return False

    async def impossible(*_args):
        raise AssertionError("mount must not happen")

    ops = MultiplexerOps(
        catalog=FleetCatalog([source], yes),
        sessions=SessionLoads(),
        loadable=no,
        mount=impossible,
        notify=impossible,
        invoke=impossible,
        health=lambda: {},
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"})
    )
    with pytest.raises(PermissionError):
        await ops.load_tools(caller, "a", [item.id])
    assert not ops.dispatchable("a", item.id)


@pytest.mark.asyncio
async def test_notification_redelivery_and_policy_revocation() -> None:
    item = CatalogItem("fleet:tool:s/read", "tool", "read", server="s")
    allowed = [True]
    notices = []

    async def source():
        return (item,)

    async def visible(_item, _caller):
        return allowed[0]

    async def notify(key):
        notices.append(key)
        return True

    async def mount(_item, _forwarder):
        return None

    async def invoke(*_args):
        return None

    sessions = SessionLoads()
    ops = MultiplexerOps(
        catalog=FleetCatalog([source], visible),
        sessions=sessions,
        loadable=visible,
        mount=mount,
        notify=notify,
        invoke=invoke,
        health=lambda: {},
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"})
    )
    await ops.load_tools(caller, "a", [item.id])
    assert sessions.status("a")["list_changed_pending"] is True
    assert await ops.redeliver_pending("a") is True
    assert sessions.status("a")["list_changed_pending"] is False
    allowed[0] = False
    assert await ops.revoke_invisible(caller, "a") == [item.id]
    assert not ops.dispatchable("a", item.id)
    assert notices == ["a", "a", "a"]


@pytest.mark.asyncio
async def test_combined_source_admits_only_registered_eg_server() -> None:
    from graph_os.fleet.catalog_sources import CombinedFleetSource

    tool = SimpleNamespace(
        entry=SimpleNamespace(
            kind="tool",
            server_name="good",
            upstream_name="read",
            summary="EG description",
        ),
        content=SimpleNamespace(body=b""),
    )
    good = SimpleNamespace(
        component=SimpleNamespace(server_name="good"),
        registration=object(),
        provides=(tool,),
    )
    bad_tool = SimpleNamespace(
        entry=SimpleNamespace(
            kind="tool", server_name="bad", upstream_name="admin", summary=""
        ),
        content=SimpleNamespace(body=b""),
    )
    bad = SimpleNamespace(
        component=SimpleNamespace(server_name="bad"),
        registration=None,
        provides=(bad_tool,),
    )

    async def verified():
        return SimpleNamespace(servers=(good, bad))

    async def live():
        return {
            "good": {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]},
            "bad": {"tools": [{"name": "admin"}]},
        }

    async def sdk():
        return ({"pack": "p", "name": "sync", "op": "ingest.sources.sync"},)

    items = await CombinedFleetSource(verified=verified, live=live, sdk=sdk)()
    assert [item.id for item in items] == ["connector:p/sync", "fleet:tool:good/read"]
    assert items[1].description == "EG description"
    assert items[1].schema == {"type": "object"}


@pytest.mark.asyncio
async def test_composition_binds_verified_reader_without_starting_probe() -> None:
    from graph_os.fleet.catalog_composition import compose_multiplexer_ops

    class Reader:
        async def read(self):
            return SimpleNamespace(servers=())

    class Mux:
        _probe_cache = {"unregistered": {"tools": [{"name": "leak"}]}}

        def status_snapshot(self):
            return {"children": {"s": {"healthy": True}}}

    async def sdk():
        return ()

    async def visible(_item, _caller):
        return True

    async def mount(_item, _forwarder):
        return None

    async def notify(_session):
        return True

    async def invoke(*_args):
        return None

    ops = compose_multiplexer_ops(
        reader=Reader(),
        mux=Mux(),
        sdk_entries=sdk,
        visible=visible,
        loadable=visible,
        mount=mount,
        notify=notify,
        invoke=invoke,
        session_key_for=lambda _caller: None,
    )
    caller = SimpleNamespace(effective_scopes=frozenset({"mcp:discover"}))
    assert await ops.find_tools(caller, browse=True) == {
        "items": [],
        "next_cursor": None,
    }
    assert ops.multiplexer_status("session")["children"]["s"]["healthy"] is True


@pytest.mark.asyncio
async def test_registry_service_api_requires_verified_session_for_mutation() -> None:
    item = CatalogItem("fleet:tool:s/read", "tool", "read", server="s")

    async def source():
        return (item,)

    async def yes(_item, _caller):
        return True

    async def mount(_item, _forwarder):
        return None

    async def notify(_session):
        return True

    async def invoke(*_args):
        return None

    ops = MultiplexerOps(
        catalog=FleetCatalog([source], yes),
        sessions=SessionLoads(),
        loadable=yes,
        mount=mount,
        notify=notify,
        invoke=invoke,
        health=lambda: {"s": {"healthy": True}},
        session_key_for=lambda caller: caller.session_key if caller.verified else None,
    )
    http = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"}),
        session_key="http-request",
        verified=False,
    )
    assert [row["id"] for row in (await ops.list(http))["items"]] == [item.id]
    assert (await ops.status(http))["session"] is None
    with pytest.raises(PermissionError, match="verified MCP session"):
        await ops.load(http, [item.id])
    mcp = SimpleNamespace(
        effective_scopes=http.effective_scopes,
        session_key="mcp-session",
        verified=True,
    )
    assert (await ops.load(mcp, [item.id]))["session_total"] == 1
    assert (await ops.status(mcp, servers=["s"]))["session"]["used"] == 1
    assert (await ops.unload(mcp, items=[item.id]))["unloaded"] == [item.id]
