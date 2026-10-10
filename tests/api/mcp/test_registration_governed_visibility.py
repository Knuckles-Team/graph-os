"""GRAPHOS-OPS-R035.1: multiplexer meta-tool registration + visibility routing.

specs/hosted-api-operations/requirements.md GRAPHOS-OPS-R035 requires the
multiplexer's meta-tool registration to be reduced to the four resident
fleet tools (find_tools, load_tools, unload_tools, multiplexer_status), and
its session-visibility middleware to route list/call requests only -- never
making an authorization decision itself. This binds both halves of that
acceptance criterion directly against graph_os.api.mcp.registration without
needing a live server: the registration module is an isolated prepared
surface that server/runtime intentionally does not call on this lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from graph_os.api.mcp.registration import (
    FLEET_OPERATIONS,
    FleetMCPBinding,
    GovernedSessionVisibility,
)


@pytest.mark.spec("GRAPHOS-OPS-R035.1")
def test_multiplexer_meta_tool_registration_is_reduced_to_four_resident_tools() -> None:
    assert frozenset(FLEET_OPERATIONS) == frozenset(
        {"find_tools", "load_tools", "unload_tools", "multiplexer_status"}
    )


@dataclass
class _FakeTool:
    name: str


class _FakeOps:
    def __init__(self, loaded: frozenset[str]) -> None:
        self._loaded = loaded

    def catalog_id_for_native(self, name: str) -> str | None:
        return name if name in self._loaded else None


class _FakeBinding:
    """Duck-types the FleetMCPBinding surface the middleware depends on."""

    def __init__(self, loaded: frozenset[str]) -> None:
        self.ops = _FakeOps(loaded)
        self._loaded = loaded

    async def visible_names(self) -> frozenset[str]:
        return self._loaded


@pytest.mark.spec("GRAPHOS-OPS-R035.1")
async def test_governed_session_visibility_only_routes_never_denies() -> None:
    """on_list_tools must filter the candidate list, raising nothing itself.

    Authorization is enforced solely by the invoke pipeline elsewhere
    (FleetMCPBinding.call_native / dispatch_verb); this middleware denies
    nothing on its own, it only narrows which already-decided tools are
    advertised.
    """
    candidates = [
        _FakeTool("find_tools"),
        _FakeTool("load_tools"),
        _FakeTool("unload_tools"),
        _FakeTool("multiplexer_status"),
        _FakeTool("loaded_child_tool"),
        _FakeTool("not_loaded_child_tool"),
    ]

    async def call_next(_context: Any) -> list[_FakeTool]:
        return candidates

    binding = _FakeBinding(loaded=frozenset({"loaded_child_tool"}))
    middleware = GovernedSessionVisibility(cast(FleetMCPBinding, binding))

    result = await middleware.on_list_tools(context=None, call_next=call_next)

    names = {tool.name for tool in result}
    assert names == {
        "find_tools",
        "load_tools",
        "unload_tools",
        "multiplexer_status",
        "loaded_child_tool",
    }
    assert "not_loaded_child_tool" not in names
