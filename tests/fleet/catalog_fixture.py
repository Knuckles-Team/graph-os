"""Test-only fleet declarations for transport and lifecycle unit tests.

Production GraphOS has no JSON catalog path.  These helpers keep child-runtime
tests small by installing explicit in-memory declarations after constructing a
multiplexer with a reader that must never be called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from graph_os.fleet.multiplexer import MCPMultiplexer, attach_fleet_loader


class _NeverRead:
    async def read(self) -> Any:
        raise AssertionError("this unit test did not compose an EG catalog")


def _install(mux: MCPMultiplexer, path: Path) -> MCPMultiplexer:
    if path.is_symlink():
        mux._catalog = {}
        return mux
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        mux._catalog = {}
        return mux
    mux._catalog = {}
    for name, config in servers.items():
        if not mux._catalog_entry_admissible(
            name, config, {"mcp-multiplexer", "graph-os"}
        ):
            continue
        admitted = mux._admit_catalog_entry(name, config, False)
        if admitted is not None:
            mux._catalog[name] = admitted
    return mux


def multiplexer_from_fixture(path: Path) -> MCPMultiplexer:
    return _install(MCPMultiplexer(_NeverRead()), path)


def attach_multiplexer_from_fixture(host: Any, path: Path) -> MCPMultiplexer:
    return _install(attach_fleet_loader(host, catalog_reader=_NeverRead()), path)


async def bind_governed_forwarder_fixture(mux: MCPMultiplexer, result: Any) -> Any:
    """Bind real invoke/gateway code to explicit synthetic read authorities.

    Only child transport, audit, identity refresh and durable-owner ports are
    fixture doubles. No production authority is supplied by this helper.
    """
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from fastmcp import FastMCP
    from graph_os.api.invoke import InvokeServices, VerifiedCaller, invoke
    from graph_os.api.invoke.audit import EffectReservation

    from graph_os.api.errors import FleetRefusal
    from graph_os.api.mcp.registration import FleetMCPBinding, register_mcp_tools
    from graph_os.api.mcp.verbs import MCPProjection
    from graph_os.api.ops.fleet import handle_fleet_call, operations
    from graph_os.api.policy import PolicyGate
    from graph_os.api.registry import Registry, Surface
    from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog
    from graph_os.fleet.gateway_ops import FleetGateway, tool_for_multiplexer_ops
    from graph_os.fleet.multiplexer import make_governed_tool_mount
    from graph_os.fleet.multiplexer_ops import MultiplexerOps
    from graph_os.fleet.session_loads import SessionLoads

    scopes = frozenset({"mcp:discover", "mcp:delegate"})
    caller = VerifiedCaller(
        principal="fixture:person",
        tenant="fixture:tenant",
        effective_scopes=scopes,
        engine_claims={
            "principal": "fixture:person",
            "tenant": "fixture:tenant",
            "scopes": list(scopes),
            "policy_version": "fixture:v1",
            "delegation": False,
        },
        principal_kind="human",
        authenticated=True,
        delegated=False,
        credential_kind="session",
        policy_revision="fixture:v1",
        request_id="fixture:request",
        session="fixture:session",
    )
    state = SimpleNamespace(caller=caller, result=result, dispatches=[], child_calls=[])
    item = CatalogItem(
        "fleet:tool:synthetic/tool",
        "tool",
        "tool",
        server="synthetic",
        schema={"type": "object"},
        annotations={"readOnlyHint": True},
    )
    mcp = FastMCP("governed-fixture")
    registry = Registry(operations())
    policy = PolicyGate(mode="none")

    async def allowed(*_args):
        return True

    async def source():
        return (item,)

    async def bound_invoke(op, params, current, surface):
        return await invoke(
            op, params, current, Surface(surface), services=state.services
        )

    def native_name(_item):
        return "synthetic__tool"

    ops = MultiplexerOps(
        catalog=FleetCatalog([source], allowed),
        sessions=SessionLoads(),
        loadable=allowed,
        callable_item=allowed,
        mount=make_governed_tool_mount(mcp, mux, native_name),
        notify=allowed,
        invoke=bound_invoke,
        health=lambda: {},
        native_name=native_name,
        session_key_for=lambda current: current.session,
    )

    async def delegated(server, tool, arguments, current):
        assert current.principal == caller.principal
        state.child_calls.append((server, tool, arguments))
        if state.result.is_error:
            raise FleetRefusal("CHILD_REFUSED", server, tool)
        return state.result.model_dump(mode="json", by_alias=True)

    gateway = FleetGateway(
        tool_for=tool_for_multiplexer_ops(ops),
        policy_check=allowed,
        delegated_call=delegated,
        catalog_ops=ops,
    )

    state.gateway = gateway

    async def current(supplied):
        assert supplied.principal == state.caller.principal
        assert supplied.tenant == state.caller.tenant
        return supplied

    @asynccontextmanager
    async def as_caller(supplied):
        assert supplied.principal == caller.principal
        yield object()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("read fixture reached an unexpected service/plan port")

    async def dispatch(op, params, context):
        state.dispatches.append(op.id)
        return await handle_fleet_call(context, params, op)

    runtime = SimpleNamespace(
        service_scopes=frozenset(),
        bindings={"fleet_gateway": gateway},
        verify_current=current,
        as_caller=as_caller,
        as_service=forbidden,
        check_subject_access=forbidden,
        dispatch=dispatch,
    )
    state.services = InvokeServices(
        registry=registry,
        runtime=runtime,
        plans=SimpleNamespace(issue=forbidden, validate=forbidden, consume=forbidden),
        policy_gate=policy,
        audit_preflight=AsyncMock(return_value="fixture:audit"),
        audit_write=AsyncMock(),
        # Only this fixture's synthetic fleet coordinator owns replay coverage.
        external_effect_operations=frozenset({"fleet.call"}),
        effects=SimpleNamespace(
            reserve=AsyncMock(
                return_value=EffectReservation("fixture:reservation", "acquired")
            ),
            complete=AsyncMock(),
        ),
        fleet_effect=gateway.effect,
    )
    projection = MCPProjection(
        registry=registry,
        services=state.services,
        resolver=None,
        caller_for_request=lambda: state.caller,
        policy_gate=policy,
        invoke=invoke,
    )
    state.binding = FleetMCPBinding(projection, ops, lambda current: current.session)
    await register_mcp_tools(mcp, mux, state.binding)
    await ops.load(caller, items=[item.id])
    return state
