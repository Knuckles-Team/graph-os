"""EH-490: the WebUI listener projects the already served A2A authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from graph_os.webui_host import a2a_routes


@dataclass
class _Route:
    path: str
    methods: set[str]
    endpoint: Any


class _App:
    def __init__(self) -> None:
        self.routes: list[_Route] = []

    def add_api_route(self, path: str, endpoint: Any, *, methods: list[str]) -> None:
        self.routes.append(_Route(path, set(methods), endpoint))


def test_webui_uses_graph_os_served_a2a_service(monkeypatch: Any) -> None:
    """Both listeners resolve the exact same process-owned WorkItem service."""
    from graph_os.a2a import mcp

    service = object()
    handlers = (object(), object())
    seen: dict[str, Any] = {}
    projection = object()
    monkeypatch.setattr(mcp, "_service", lambda: service)
    monkeypatch.setattr(mcp, "_operation_projection", lambda: projection)

    def create_handlers(
        *, service: object, authenticator: object, operation_projection: object
    ) -> tuple[Any, Any]:
        seen["service"] = service
        seen["authenticator"] = authenticator
        seen["operation_projection"] = operation_projection
        return handlers

    monkeypatch.setattr(a2a_routes, "create_a2a_handlers", create_handlers)
    app = _App()
    a2a_routes.register_a2a_routes(app)

    assert seen["service"] is service
    assert isinstance(seen["authenticator"], a2a_routes.AmbientA2AAuthenticator)
    assert seen["operation_projection"] is projection
    assert [(route.path, route.methods, route.endpoint) for route in app.routes] == [
        ("/.well-known/agent-card.json", {"GET"}, handlers[0]),
        ("/a2a", {"POST"}, handlers[1]),
    ]


def test_a2a_route_collision_refuses_second_authority(monkeypatch: Any) -> None:
    app = _App()
    app.routes.append(_Route("/a2a", {"POST"}, object()))
    monkeypatch.setattr(
        a2a_routes,
        "create_a2a_handlers",
        lambda **_: pytest.fail("must refuse before building A2A handlers"),
    )
    with pytest.raises(RuntimeError, match="already registered"):
        a2a_routes.register_a2a_routes(app, service=object())  # type: ignore[arg-type]
