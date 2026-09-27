"""The legacy GraphOS SPARQL route fails closed until EG authority is bound."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace

from fastapi import FastAPI

from graph_os.gateway.graph_api import _mount_sparql_route


def test_sparql_route_requires_read_scope_and_reports_unavailable(monkeypatch) -> None:
    scopes: list[str] = []
    session_module = ModuleType("agent_utilities.api.session")
    session_module.__dict__["resolve_session"] = lambda *, required_scope: (
        scopes.append(required_scope)
    )
    monkeypatch.setitem(sys.modules, "agent_utilities.api.session", session_module)
    app = FastAPI()
    _mount_sparql_route(app)

    route = next(route for route in app.routes if route.path == "/api/sparql")
    response = asyncio.run(
        route.endpoint(
            SimpleNamespace(query_params={"query": "SELECT * WHERE {}"}, method="GET")
        )
    )

    assert scopes == ["kg:read"]
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["code"] == "sparql_unavailable"
    assert "results" not in payload


def test_sparql_route_rejects_missing_query(monkeypatch) -> None:
    session_module = ModuleType("agent_utilities.api.session")
    session_module.__dict__["resolve_session"] = lambda *, required_scope: None
    monkeypatch.setitem(sys.modules, "agent_utilities.api.session", session_module)
    app = FastAPI()
    _mount_sparql_route(app)

    route = next(route for route in app.routes if route.path == "/api/sparql")
    response = asyncio.run(
        route.endpoint(SimpleNamespace(query_params={}, method="GET"))
    )

    assert response.status_code == 400
