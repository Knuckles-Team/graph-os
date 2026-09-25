"""EH-405: every elevation surface keeps the two-person, verified-identity rules.

Agents (MCP/REST ``graph_elevation``, chat, A2A) request, list and revoke;
only the operator console route approves, as the signed-in session, never as a
body field, and never for the approver's own request.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.security.elevation import APPROVAL_SCOPE
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.applications import Starlette

from graph_os.a2a.application import create_a2a_application
from graph_os.a2a.service import A2AService
from graph_os.gateway.elevation import mount_elevation_routes
from graph_os.mcp_server import runtime
from graph_os.mcp_server.elevation import (
    ElevationToolRequest,
    register_elevation_tools,
)
from tests.mcp_server.elevation_fakes import ElevationClient
from tests.mcp_server.test_policy_release import _Mcp

HOUR_MS = 3_600_000
ASK = {
    "scopes": [{"graph": "tenant-a", "action": "write"}],
    "span_ms": HOUR_MS,
    "justification": "repair incident 42",
}


def _session(agent: str, *scopes: str, delegation: tuple[str, ...] = ()) -> Any:
    claims = {
        "agent_id": agent,
        "principal": agent,
        "tenant": "tenant-a",
        "delegation": list(delegation),
        "scopes": sorted(scopes),
    }
    return SimpleNamespace(
        tenant="tenant-a",
        scopes=frozenset(scopes),
        engine_verified_context=lambda: claims,
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:
    client = ElevationClient()
    state = {"session": _session("alice", "kg:write")}

    @contextlib.contextmanager
    def scope() -> Any:
        yield state["session"]

    monkeypatch.setattr(runtime, "verified_tool_session_scope", scope)
    monkeypatch.setattr(runtime, "graph_client", lambda tenant: client)
    monkeypatch.setattr(
        "agent_utilities.api.session.resolve_session",
        lambda required_scope=None: state["session"],
    )
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application",
        lambda: SimpleNamespace(graph_client=lambda tenant: client),
    )
    mcp = _Mcp()
    prior = runtime.REGISTERED_TOOLS.get("graph_elevation")
    register_elevation_tools(mcp)

    async def tool(**fields: Any) -> Any:
        return await mcp.tools["graph_elevation"](ElevationToolRequest(**fields))

    yield SimpleNamespace(tool=tool, client=client, state=state)
    runtime.REGISTERED_TOOLS.pop("graph_elevation", None)
    if prior is not None:
        runtime.REGISTERED_TOOLS["graph_elevation"] = prior


def _console(world: Any) -> TestClient:
    app = Starlette()
    mount_elevation_routes(app, prefix="/api")
    return TestClient(app)


async def _requested(world: Any) -> dict[str, Any]:
    world.state["session"] = _session("alice", "kg:write")
    return await world.tool(action="request", **ASK)


async def test_an_agent_requests_lists_and_revokes_as_its_verified_self(
    world: Any,
) -> None:
    asked = await _requested(world)
    assert asked["status"] == "requested" and asked["grantee"] == "alice"
    assert asked["own"], "the requester's console will not offer to approve it"
    listed = await world.tool(action="list")
    assert [row["elevation_id"] for row in listed] == [asked["elevation_id"]]
    revoked = await world.tool(action="revoke", elevation_id=asked["elevation_id"])
    assert revoked["status"] == "revoked"
    assert world.client.ops == ["request", "list", "revoke"]
    assert all(claims["agent_id"] == "alice" for claims in world.client.bound)


def test_the_agent_tool_has_no_approve_action_and_takes_no_identity() -> None:
    with pytest.raises(ValidationError):
        ElevationToolRequest.model_validate({"action": "approve", "elevation_id": "e"})
    with pytest.raises(ValidationError):
        ElevationToolRequest.model_validate({"action": "list", "actor": "bob"})


def test_the_tool_has_its_rest_twin() -> None:
    assert runtime.ACTION_TOOL_ROUTES["graph_elevation"] == "/graph/elevation"


async def test_the_console_approves_as_the_signed_in_operator(world: Any) -> None:
    asked = await _requested(world)
    world.state["session"] = _session("bob", "kg:read", APPROVAL_SCOPE)
    response = _console(world).post(
        "/api/elevations/approve",
        json={
            "elevation_id": asked["elevation_id"],
            "request_digest": asked["request_digest"],
        },
    )
    assert response.status_code == 200
    elevation = response.json()["elevation"]
    assert elevation["status"] == "active"
    assert HOUR_MS - 60_000 < elevation["remaining_ms"] <= HOUR_MS, "countdown data"
    assert world.client.bound[-1]["agent_id"] == "bob"


@pytest.mark.parametrize(
    ("session", "code"),
    [
        (_session("alice", APPROVAL_SCOPE), "ELEVATION_SELF_APPROVAL"),
        (_session("bob", "kg:admin", "*"), "ELEVATION_APPROVER_SCOPE"),
        (
            _session("bob", APPROVAL_SCOPE, delegation=("bob", "agent:chat:1")),
            "ELEVATION_APPROVER_DELEGATED",
        ),
    ],
)
async def test_the_console_refuses_what_eg_would_refuse(
    world: Any, session: Any, code: str
) -> None:
    asked = await _requested(world)
    world.state["session"] = session
    response = _console(world).post(
        "/api/elevations/approve",
        json={
            "elevation_id": asked["elevation_id"],
            "request_digest": asked["request_digest"],
        },
    )
    assert (response.status_code, response.json()["code"]) == (403, code)
    assert "approve" not in world.client.ops


async def test_an_approver_named_in_the_body_is_refused(world: Any) -> None:
    asked = await _requested(world)
    world.state["session"] = _session("bob", APPROVAL_SCOPE)
    response = _console(world).post(
        "/api/elevations/approve",
        json={
            "elevation_id": asked["elevation_id"],
            "request_digest": asked["request_digest"],
            "approver": "carol",
        },
    )
    assert (response.status_code, response.json()["code"]) == (400, "invalid_request")


class _Authenticator:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def authenticate(self, request: Any, *, scope: str) -> None:
        self.scopes.append(scope)


def _a2a() -> tuple[TestClient, _Authenticator]:
    auth = _Authenticator()
    service = A2AService(authority=SimpleNamespace(), router=SimpleNamespace())
    return TestClient(create_a2a_application(service=service, authenticator=auth)), auth


def _rpc(client: TestClient, method: str, params: dict[str, Any]) -> Any:
    return client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": method, "method": method, "params": params},
    )


def test_an_a2a_peer_requests_but_can_never_approve(world: Any) -> None:
    client, auth = _a2a()
    asked = _rpc(client, "elevation/request", ASK)
    assert asked.status_code == 200
    elevation = asked.json()["result"]["elevations"][0]
    assert elevation["grantee"] == "alice"
    world.state["session"] = _session("bob", APPROVAL_SCOPE)
    refused = _rpc(
        client,
        "elevation/approve",
        {
            "elevation_id": elevation["elevation_id"],
            "request_digest": elevation["request_digest"],
        },
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == -32004
    assert world.client.ops == ["request"]
    assert auth.scopes == ["kg:write", "kg:read"]


def test_the_agent_card_advertises_the_elevation_skill() -> None:
    service = A2AService(authority=SimpleNamespace(), router=SimpleNamespace())
    skills = {skill.id for skill in service.agent_card("/a2a").skills}
    assert "graph-os-elevation" in skills
