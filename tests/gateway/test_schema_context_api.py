"""Tests for the schema-context REST route
(GRAPHOS-DATA-MARKET-R005, GDM-03; SC-05 in
``specs/data-and-market-projections/test-spec.md``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.gateway.schema_context_api import register_schema_context_routes

PATH = "/api/graph/schema/context"


def _app() -> FastAPI:
    app = FastAPI()
    register_schema_context_routes(app)
    return app


def _valid_body() -> dict[str, object]:
    return {
        "source_id": "sample_app",
        "schema": "public",
        "object": "customer",
        "intent": "definition",
    }


def test_route_is_registered_exactly_once() -> None:
    schema = _app().openapi()

    matches = [path for path in schema["paths"] if path == PATH]

    assert len(matches) == 1
    assert schema["paths"][PATH].keys() == {"post"}


def test_malformed_body_returns_400() -> None:
    client = TestClient(_app())

    response = client.post(PATH, json=_valid_body() | {"intent": "delete"})

    assert response.status_code == 400


def test_non_object_body_returns_400() -> None:
    client = TestClient(_app())

    response = client.post(PATH, json=["not", "an", "object"])

    assert response.status_code == 400


def test_missing_engine_capability_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-04 over REST: no engine capability yields 503, zero engine calls."""

    engine = Mock(spec=[])
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application",
        lambda: Mock(engine=Mock(return_value=engine)),
    )
    client = TestClient(_app())

    response = client.post(PATH, json=_valid_body())

    assert response.status_code == 503
    assert engine.mock_calls == []


def test_available_capability_dispatches_through_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = Mock()
    engine.schema_context.schema_context.return_value = {"sentinel": "answer"}
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application",
        lambda: Mock(engine=Mock(return_value=engine)),
    )
    client = TestClient(_app())

    response = client.post(PATH, json=_valid_body())

    assert response.status_code == 200
    engine.schema_context.schema_context.assert_called_once()


def test_register_schema_context_routes_is_idempotent() -> None:
    app = _app()
    before = len(app.routes)

    register_schema_context_routes(app)

    assert len(app.routes) == before


def test_register_schema_context_routes_on_plain_starlette() -> None:
    added: list[dict[str, Any]] = []

    class _PlainApp:
        routes: list[Any] = []

        def add_route(self, path: str, endpoint: Any, methods: list[str]) -> None:
            added.append({"path": path, "methods": methods})

    register_schema_context_routes(_PlainApp(), prefix="/api")

    assert added == [{"path": PATH, "methods": ["POST"]}]
