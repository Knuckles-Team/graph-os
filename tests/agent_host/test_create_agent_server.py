"""GRAPHOS-HOST-R009 / AU-BOUNDARY-R015: connector agent host wiring tests.

Exercises the real ``build_agent_host_app`` entry point a connector's
``agent_server.py`` reaches through ``create_agent_server`` -- health,
agent-card discovery, and a bounded A2A ``message/send`` exchange -- with a
stub executor standing in for a live model/MCP call.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from graph_os.agent_host import build_agent_host_app


def _stub_executor(graph: Any, graph_config: dict, query: str) -> dict:
    assert graph == "stub-graph"
    assert graph_config == {"valid_domains": []}
    return {"output": f"echo: {query}"}


def _client() -> TestClient:
    app = build_agent_host_app(
        graph="stub-graph",
        graph_config={"valid_domains": []},
        name="Stub Connector Agent",
        description="A stub connector agent for wiring tests.",
        executor=_stub_executor,
    )
    return TestClient(app)


@pytest.mark.spec("GRAPHOS-HOST-R009")
def test_health() -> None:
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.spec("GRAPHOS-HOST-R009")
def test_agent_card() -> None:
    response = _client().get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Stub Connector Agent"
    assert card["protocolVersion"] == "0.3.0"


@pytest.mark.spec("GRAPHOS-HOST-R009")
def test_message_send_runs_the_executor_and_returns_a_completed_task() -> None:
    response = _client().post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": 1,
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                    "messageId": "m-1",
                }
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    result = body["result"]
    assert result["status"]["state"] == "completed"
    agent_message = result["history"][-1]
    assert agent_message["role"] == "agent"
    assert agent_message["parts"][0]["text"] == "echo: hello"


def test_message_send_failure_is_reported_as_an_a2a_error_not_an_http_error() -> None:
    def _raising_executor(graph: Any, graph_config: dict, query: str) -> dict:
        raise RuntimeError("boom")

    app = build_agent_host_app(
        graph="stub-graph",
        graph_config={"valid_domains": []},
        executor=_raising_executor,
    )
    response = TestClient(app).post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": 2,
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                }
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 2
    assert body["error"]["code"] == -32000


def test_unknown_method_is_rejected() -> None:
    response = _client().post(
        "/a2a",
        json={"jsonrpc": "2.0", "method": "tasks/get", "id": 3, "params": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32601
