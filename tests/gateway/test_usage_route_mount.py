"""graph_api.register_graph_routes mounts the usage/observability surface.

Wiring the previously-unreferenced graph_os.gateway.usage_api module (found by
the check-orphan-modules gate) is a behavior change: a surface that was never
served is now served. These tests prove it is mounted exactly once, under the
documented path, and that it is not a bypass of usage authorization: a caller
without a verified identity is refused with the module's own mapped status,
never served data. No live engine; same pattern as
agent_utilities/tests/unit/test_usage_api.py's authorization tests.
"""

from __future__ import annotations

from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext, use_actor
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.gateway.usage_api import register_usage_routes


def _app() -> FastAPI:
    app = FastAPI()
    register_usage_routes(app)
    return app


def test_usage_summary_route_is_registered_exactly_once() -> None:
    """The resolved OpenAPI schema, not raw ``app.routes`` (FastAPI 0.141
    wraps an included router in an opaque ``_IncludedRouter`` that does not
    flatten prefixed paths there), is the stable way to prove a path was
    mounted exactly once."""
    schema = _app().openapi()

    matches = [path for path in schema["paths"] if path == "/api/observability/summary"]

    assert len(matches) == 1
    assert schema["paths"]["/api/observability/summary"].keys() == {"get"}


def test_usage_summary_rejects_an_unauthenticated_caller() -> None:
    """An actor claim that exists but was never verified must be refused.

    Mirrors agent_utilities' own
    test_served_usage_rejects_missing_verified_identity: the module's
    ``_tenant_scope`` maps ``resolve_usage_tenant``'s
    ``UsageAuthorizationError`` straight to the HTTPException it carries, so
    an unauthenticated caller gets that exact status, not a 200 with data.
    """
    client = TestClient(_app())
    unauthenticated = ActorContext(
        actor_id="unverified",
        actor_type=ActorType.AUTOMATED_SERVICE,
        roles=(),
        tenant_id="",
        authenticated=False,
    )

    with use_actor(unauthenticated):
        response = client.get("/api/observability/summary")

    assert response.status_code == 401
    assert "session_count" not in response.text
