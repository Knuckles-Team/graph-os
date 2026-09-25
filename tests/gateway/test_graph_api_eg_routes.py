"""SPARQL and SQL-catalog routes answered by the generated EG client."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.gateway import graph_api
from graph_os.gateway.ports import configure_gateway_application

_CLAIMS = {"tenant": "tenant-a", "principal": "user-a"}


class _Session:
    tenant = "tenant-a"

    def engine_verified_context(self) -> dict[str, str]:
        return dict(_CLAIMS)


class _Rdf:
    def __init__(self, graph: str, bound: list[Any]) -> None:
        self._graph = graph
        self._bound = bound

    async def sparql(self, query: str) -> list[dict[str, str | None]]:
        assert self._bound, "SPARQL ran without verified claims bound"
        return [{"g": self._graph, "q": query}]


class _Query:
    def __init__(self, bound: list[Any], rows: dict[str, Any]) -> None:
        self._bound = bound
        self._rows = rows

    async def sql(self, statement: str) -> Any:
        assert self._bound, "SQL ran without verified claims bound"
        for marker, rows in self._rows.items():
            if marker in statement:
                return rows
        raise AssertionError(statement)


class _Client:
    def __init__(self, graph: str, rows: dict[str, Any]) -> None:
        self.bound: list[Any] = []
        self.rdf = _Rdf(graph, self.bound)
        self.query = _Query(self.bound, rows)

    @contextlib.contextmanager
    def use_verified_context(self, claims: Any) -> Iterator[None]:
        self.bound.append(claims)
        try:
            yield
        finally:
            self.bound.pop()


_CATALOG_ROWS = {
    "information_schema.tables": [
        {
            "table_catalog": "eg",
            "table_schema": "main",
            "table_name": "nodes",
            "table_type": "BASE TABLE",
        }
    ],
    "information_schema.columns": [
        {
            "table_catalog": "eg",
            "table_schema": "main",
            "table_name": "nodes",
            "column_name": "id",
            "ordinal_position": 1,
            "is_nullable": "NO",
            "data_type": "text",
        }
    ],
    "information_schema.key_column_usage": [],
}


class _Application:
    def __init__(self) -> None:
        self.clients: dict[str, _Client] = {}

    async def execute_tool(self, tool: str, /, **kwargs: Any) -> Any:
        raise AssertionError(tool)

    def engine(self) -> Any:
        raise AssertionError("routes must not reach the process engine")

    def graph_client(self, graph: str) -> _Client:
        return self.clients.setdefault(graph, _Client(graph, _CATALOG_ROWS))

    def ensure_tools_registered(self) -> None:
        return None

    def mount_rest_routes(self, app: Any, *, prefix: str) -> None:
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Application]:
    application = _Application()
    configure_gateway_application(application)
    monkeypatch.setattr(
        "agent_utilities.api.session.resolve_session",
        lambda *args, **kwargs: _Session(),
    )
    app = Starlette()
    graph_api._mount_sparql_route(app, prefix="/api")
    graph_api._mount_sql_schema_route(app, prefix="/api")
    return TestClient(app), application


def test_sparql_unions_tenant_then_commons_under_verified_claims(client) -> None:
    http, application = client

    response = http.get("/api/sparql", params={"query": "SELECT * WHERE {}"})

    assert response.status_code == 200
    rows = response.json()["results"]["bindings"]
    assert [row["g"] for row in rows] == ["tenant-a", "__commons__"]
    assert set(application.clients) == {"tenant-a", "__commons__"}


def test_sparql_rejects_missing_query(client) -> None:
    http, _application = client

    assert http.post("/api/sparql", json={}).status_code == 400


def test_sql_schema_projects_engine_information_schema(client) -> None:
    http, application = client

    response = http.post("/api/graph/sql-schema", json={"schema": "main"})

    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"catalogs": 1, "schemas": 1, "tables": 1, "columns": 1}
    assert body["capabilities"] == {"primary_keys": False, "nullability": False}
    assert set(application.clients) == {"tenant-a"}


def test_sql_schema_rejects_non_identifier_filter(client) -> None:
    http, _application = client

    response = http.post("/api/graph/sql-schema", json={"schema": "x; DROP"})

    assert response.status_code == 422
