"""G6 resident MCP discovery and loaded forwarder authority contract."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.api.policy import fleet_resource
from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog
from graph_os.fleet.multiplexer import _make_forwarder, _register_meta_tools
from graph_os.fleet.multiplexer_ops import MultiplexerOps
from graph_os.fleet.session_loads import SessionLoads
from tests.api.test_authority_parity import MATRIX, _caller, _gate


def test_resident_caller_keeps_verified_policy_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident tools receive the same complete caller as governed op invoke."""
    from graph_os.fleet import multiplexer
    from graph_os.fleet.fleet_authority import FleetCaller
    from graph_os.mcp_server import runtime

    caller = _caller("admin")
    fleet_caller = FleetCaller(
        subject=caller.principal,
        client_id=caller.principal,
        tenant=caller.tenant,
        capabilities=caller.effective_scopes,
        groups=(),
        transport="local",
    )
    monkeypatch.setattr(multiplexer, "resolve_fleet_caller", lambda: fleet_caller)
    monkeypatch.setattr(
        runtime,
        "served_api",
        lambda: (SimpleNamespace(caller_for_request=lambda: caller), object()),
    )
    assert multiplexer._ops_caller() is caller
    assert multiplexer._ops_caller().policy_revision == "fixture-rev-1"
    assert multiplexer._ops_caller().engine_claims["principal"] == caller.principal

    mismatched = replace(caller, tenant="different-tenant")
    monkeypatch.setattr(
        runtime,
        "served_api",
        lambda: (SimpleNamespace(caller_for_request=lambda: mismatched), object()),
    )
    with pytest.raises(Exception, match="identity mismatch"):
        multiplexer._ops_caller()

    broader = replace(
        caller, effective_scopes=caller.effective_scopes | {"fleet:write"}
    )
    monkeypatch.setattr(
        runtime,
        "served_api",
        lambda: (SimpleNamespace(caller_for_request=lambda: broader), object()),
    )
    with pytest.raises(Exception, match="identity mismatch"):
        multiplexer._ops_caller()

    incomplete = replace(caller, policy_revision="")
    monkeypatch.setattr(
        runtime,
        "served_api",
        lambda: (SimpleNamespace(caller_for_request=lambda: incomplete), object()),
    )
    with pytest.raises(Exception, match="caller unavailable"):
        multiplexer._ops_caller()


@pytest.mark.parametrize("principal", MATRIX["principals"])
async def test_resident_find_tools_matches_governed_fleet_catalog(
    monkeypatch: pytest.MonkeyPatch, principal: str
) -> None:
    """Call the registered resident body using the same embedded PDP as HTTP."""
    from graph_os.fleet import fleet_authority, multiplexer
    from graph_os.fleet.fleet_authority import FleetCaller

    items = tuple(
        CatalogItem(
            id=item_id,
            kind="tool",
            name=item_id.rsplit("/", 1)[-1],
            server="sample",
            required_scopes=frozenset(case["scopes"]),
        )
        for item_id, case in MATRIX["fleet_items"].items()
    )
    caller = _caller(principal)
    gate = _gate()

    async def source() -> tuple[CatalogItem, ...]:
        return items

    async def visible(item: CatalogItem, selected: Any) -> bool:
        resource = fleet_resource(
            "tool", f"{item.server}/{item.name}", required_scopes=item.required_scopes
        )
        return (await gate.visible([resource], selected))[0]

    async def yes(*_args: Any) -> bool:
        return True

    async def mount(*_args: Any) -> None:
        return None

    async def invoke(*_args: Any) -> None:
        return None

    catalog = FleetCatalog((source,), visible)
    ops = MultiplexerOps(
        catalog=catalog,
        sessions=SessionLoads(),
        loadable=yes,
        mount=mount,
        notify=yes,
        invoke=invoke,
        health=lambda: {},
    )
    mux = SimpleNamespace(_multiplexer_ops=ops, _global_visible=set())
    registered: dict[str, Any] = {}
    mcp = SimpleNamespace(add_tool=lambda tool: registered.update({tool.name: tool}))
    monkeypatch.setattr(multiplexer, "_ops_caller", lambda: caller)
    monkeypatch.setattr(
        fleet_authority,
        "resolve_fleet_caller",
        lambda: FleetCaller(
            subject=caller.principal,
            client_id=caller.principal,
            tenant=caller.tenant,
            capabilities=caller.effective_scopes,
            groups=(),
            transport="local",
        ),
    )
    _register_meta_tools(mcp, mux)

    actual = await registered["find_tools"].fn(browse=True)
    expected = await catalog.search(caller, browse=True)
    assert actual.structured_content == expected
    assert {row["id"] for row in expected["items"]} == {
        item_id
        for item_id, case in MATRIX["fleet_items"].items()
        if principal in case["allow"]
    }


async def test_loaded_native_forwarder_rechecks_policy_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered native call resolves a fresh caller and cannot survive revocation."""
    from graph_os.fleet import multiplexer

    item = CatalogItem(
        id="fleet:tool:sample/public",
        kind="tool",
        name="public",
        server="sample",
        required_scopes=frozenset({"graph:read"}),
    )
    gate = _gate()
    principal = _caller("reader")
    caller = replace(
        principal,
        effective_scopes=principal.effective_scopes | {"mcp:delegate"},
    )
    allowed = [True]
    calls: list[tuple[str, dict[str, Any], Any, str]] = []

    async def source() -> tuple[CatalogItem, ...]:
        return (item,)

    async def visible(selected: CatalogItem, active: Any) -> bool:
        resource = fleet_resource(
            "tool",
            f"{selected.server}/{selected.name}",
            required_scopes=selected.required_scopes,
        )
        return allowed[0] and (await gate.visible([resource], active))[0]

    async def yes(*_args: Any) -> bool:
        return True

    async def mount(*_args: Any) -> None:
        return None

    async def invoke(
        op: str, params: Any, active: Any, surface: str
    ) -> dict[str, bool]:
        calls.append((op, params, active, surface))
        return {"ok": True}

    ops = MultiplexerOps(
        catalog=FleetCatalog((source,), visible),
        sessions=SessionLoads(),
        loadable=yes,
        mount=mount,
        notify=yes,
        invoke=invoke,
        health=lambda: {},
        native_name=lambda selected: f"sample__{selected.name}",
    )
    await ops.load_tools(caller, "verified-session", (item.id,))
    mux = SimpleNamespace(
        _multiplexer_ops=ops,
        tool_dispatchable=lambda name: ops.dispatchable("verified-session", name),
    )
    monkeypatch.setattr(multiplexer, "_ops_caller", lambda: caller)
    forward = _make_forwarder(mux, "sample__public")
    assert await forward(query="x") == {"ok": True}
    assert calls == [
        (
            "fleet.call",
            {"server": "sample", "tool": "public", "arguments": {"query": "x"}},
            caller,
            "mcp",
        )
    ]

    allowed[0] = False
    with pytest.raises(Exception, match="unavailable"):
        await forward(query="x")
    assert len(calls) == 1

    await ops.invalidate_policy_revision("revoked")
    assert not ops.dispatchable("verified-session", "sample__public")
    with pytest.raises(Exception, match="not loaded"):
        await forward(query="x")
    assert len(calls) == 1
