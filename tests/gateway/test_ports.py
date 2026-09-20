"""Live-path tests for the G1 gateway to G2 application boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from graph_os.gateway import ontology_api
from graph_os.gateway.ports import (
    configure_gateway_application,
    gateway_application,
)


class _Application:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(self, tool: str, /, **kwargs: Any) -> Any:
        self.calls.append((tool, kwargs))
        return {"tool": tool, **kwargs}

    def engine(self) -> object:
        return self

    def ensure_tools_registered(self) -> None:
        return None

    def mount_rest_routes(self, app: Any, *, prefix: str) -> None:
        app.append(prefix)

    def remote_oauth_grant_bindings(self, actor: Any) -> tuple[Any, ...]:
        return ()

    def toggle_states_batch(
        self, engine: Any, items: Sequence[tuple[str, str]]
    ) -> Mapping[tuple[str, str], bool]:
        return dict.fromkeys(items, True)


@pytest.mark.asyncio
async def test_ontology_route_dispatches_through_configured_application_port() -> None:
    application = _Application()
    configure_gateway_application(application)

    result = await ontology_api._call(
        "ontology_value_types", action="describe", name="email"
    )

    assert result == {
        "tool": "ontology_value_types",
        "action": "describe",
        "name": "email",
    }
    assert application.calls == [
        ("ontology_value_types", {"action": "describe", "name": "email"})
    ]
    assert gateway_application() is application
