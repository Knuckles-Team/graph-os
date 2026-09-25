"""EH-629: fleet authority is exact-scope, has no stdio bypass, and fails closed."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from graph_os.fleet.fleet_authority import (
    FleetKind,
    require_fleet_capability,
    resolve_fleet_caller,
)
from graph_os.fleet.multiplexer import (
    SessionVisibilityMiddleware,
    _register_meta_tools,
)
from tests.fleet.catalog_fixture import multiplexer_from_fixture
from tests.fleet.conftest import FLEET_SCOPES, fleet_session

ADMIN_ROLES = ("admin", "kg:admin", "mcp:admin")
DISCOVER_TOOLS = {"find_tools", "list_catalog", "multiplexer_status"}
DELEGATE_TOOLS = {"load_tools", "unload_tools"}
CHILD = "container-manager-mcp"
CHILD_TOOL = "cm__container_operations"


def _fake_http(monkeypatch: pytest.MonkeyPatch, token: object) -> None:
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_request", lambda: object()
    )
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)


@pytest.mark.parametrize("kind", ["discover", "delegate"])
def test_admin_scopes_do_not_substitute_for_fleet_scopes(kind: str) -> None:
    with fleet_session(*ADMIN_ROLES), pytest.raises(ToolError, match="capability"):
        require_fleet_capability(kind)


@pytest.mark.parametrize(
    ("held", "allowed", "refused"),
    [
        ("mcp:discover", "discover", "delegate"),
        ("mcp:delegate", "delegate", "discover"),
    ],
)
def test_each_kind_needs_its_own_exact_scope(
    held: str, allowed: str, refused: str
) -> None:
    with fleet_session(held):
        require_fleet_capability(allowed)
        with pytest.raises(ToolError, match=f"fleet {refused} capability"):
            require_fleet_capability(refused)


def test_child_scopes_are_exact_and_admin_does_not_cover_them() -> None:
    with fleet_session("mcp:delegate", *ADMIN_ROLES):
        with pytest.raises(ToolError, match="Child MCP capability scope"):
            require_fleet_capability("delegate", ["svc:read"])
    with fleet_session("mcp:delegate", "svc:read"):
        require_fleet_capability("delegate", ["svc:read"])


def test_local_call_without_a_bound_session_is_refused() -> None:
    assert resolve_fleet_caller() is None
    with pytest.raises(ToolError, match="fleet discover capability"):
        require_fleet_capability("discover")


def test_stdio_runs_with_its_own_minted_process_scopes() -> None:
    """A stdio process runs fleet calls with its own minted process principal
    (no early return): AU's ambient local grant holds exactly mcp:discover +
    mcp:delegate, so it passes the fleet gates on those scopes alone, and a
    child's extra required scope is still refused."""
    from agent_utilities.api import use_session
    from agent_utilities.security.request_identity import mint_local_process_session

    with use_session(mint_local_process_session()):
        caller = resolve_fleet_caller()
        assert caller is not None and caller.transport == "local"
        assert {FleetKind.DISCOVER.scope, FleetKind.DELEGATE.scope} <= (
            caller.capabilities
        )
        require_fleet_capability("discover")
        require_fleet_capability("delegate")
        with pytest.raises(ToolError, match="Child MCP capability scope"):
            require_fleet_capability("delegate", ["svc:read"])
    with fleet_session(*FLEET_SCOPES):
        require_fleet_capability("discover")
        require_fleet_capability("delegate")


def test_unknown_fleet_kind_is_refused() -> None:
    with fleet_session(*FLEET_SCOPES), pytest.raises(ToolError, match="Unknown"):
        require_fleet_capability("manage")


def test_http_caller_uses_the_verified_bearer_exact_scopes(monkeypatch) -> None:
    token = SimpleNamespace(scopes=["mcp:discover"], claims=None, client_id="cli")
    _fake_http(monkeypatch, token)
    require_fleet_capability("discover")
    with pytest.raises(ToolError, match="fleet delegate capability"):
        require_fleet_capability("delegate")


@pytest.mark.parametrize("token", [None, SimpleNamespace(scopes=list(ADMIN_ROLES))])
def test_http_caller_without_fleet_scope_is_refused(monkeypatch, token) -> None:
    _fake_http(monkeypatch, token)
    with fleet_session(*FLEET_SCOPES):  # an ambient session never stands in
        with pytest.raises(ToolError, match="fleet discover capability"):
            require_fleet_capability("discover")


def _served_mux(tmp_path, required_scopes: list[str] | None = None):
    server: dict[str, object] = {"command": "python", "args": ["-m", CHILD]}
    if required_scopes is not None:
        server["required_scopes"] = required_scopes
    path = tmp_path / "mcp_config.json"
    path.write_text(json.dumps({"mcpServers": {CHILD: server}}), encoding="utf-8")
    mux = multiplexer_from_fixture(path)
    mcp = FastMCP("fleet-authority")

    @mcp.tool(name="native_tool")
    def _native() -> str:
        return "ok"

    _register_meta_tools(mcp, mux)
    mux.admit_native_tools(mcp)
    mcp.add_middleware(SessionVisibilityMiddleware(mux, mcp))
    return mux, mcp


@pytest.mark.parametrize(
    ("roles", "visible"),
    [
        ((), set()),
        (ADMIN_ROLES, set()),
        (("mcp:discover",), DISCOVER_TOOLS),
        (("mcp:delegate",), DELEGATE_TOOLS),
        (FLEET_SCOPES, DISCOVER_TOOLS | DELEGATE_TOOLS),
    ],
)
async def test_tool_list_and_call_agree_on_fleet_scopes(
    tmp_path, roles: tuple[str, ...], visible: set[str]
) -> None:
    """Eunomia off: the served list is scope-filtered, and each listed meta-tool
    is exactly the set a call admits past the visibility gate."""
    _mux, mcp = _served_mux(tmp_path)
    with fleet_session(*roles):
        async with Client(mcp) as client:
            listed = {tool.name for tool in await client.list_tools()}
            assert listed == visible | {"native_tool"}
            for name in (DISCOVER_TOOLS | DELEGATE_TOOLS) - visible:
                with pytest.raises(ToolError, match="scopes|capability"):
                    await client.call_tool(name, {})
            assert (await client.call_tool("native_tool", {})).data == "ok"


async def test_unknown_tool_is_refused_by_the_gate(tmp_path) -> None:
    mux, mcp = _served_mux(tmp_path)

    @mcp.tool(name="registered_after_attach")
    def _late() -> str:
        return "late"

    with fleet_session(*FLEET_SCOPES):
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "registered_after_attach" not in names
            with pytest.raises(ToolError, match="not loaded"):
                await client.call_tool("registered_after_attach", {})
        mux.admit_native_tools(mcp)
        async with Client(mcp) as client:
            result = await client.call_tool("registered_after_attach", {})
            assert result.data == "late"


def test_fleet_tool_needs_delegate_and_its_child_scopes(tmp_path) -> None:
    mux, _mcp = _served_mux(tmp_path, required_scopes=["svc:read"])
    assert mux._server_for_prefixed(CHILD_TOOL) == CHILD
    delegate = frozenset({"mcp:delegate"})
    assert not mux.tool_scope_allowed(CHILD_TOOL, delegate)
    assert not mux.tool_scope_allowed(CHILD_TOOL, frozenset({"svc:read", *ADMIN_ROLES}))
    assert mux.tool_scope_allowed(CHILD_TOOL, delegate | {"svc:read"})
