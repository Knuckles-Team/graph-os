"""Unary A2A transport contracts."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient

from graph_os.a2a.application import create_a2a_application


class Authenticator:
    def __init__(self) -> None:
        self.calls = 0

    async def authenticate(self, request: Any) -> None:
        self.calls += 1
        if request.headers.get("Authorization") != "Bearer verified":
            raise HTTPException(status_code=401)


class Service:
    async def send_message(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": "task-1", "status": {"state": "submitted"}, **kwargs}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        return {"id": task_id, "status": {"state": "working"}}

    async def list_tasks(self, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        return ([{"id": "task-1"}], "next")

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        return {"id": task_id, "status": {"state": "canceled"}}


def _app() -> tuple[Any, Authenticator]:
    auth = Authenticator()
    app = create_a2a_application(
        service=Service(),
        authenticator=auth,
        name="Graph OS",
        description="Canonical graph execution",
        version="1.0.0",
        endpoint_url="https://graph.example.test/a2a",
    )
    return app, auth


def test_agent_card_is_authenticated_truthful_and_has_one_path() -> None:
    app, auth = _app()
    client = TestClient(app)
    assert client.get("/.well-known/agent-card.json").status_code == 401
    response = client.get(
        "/.well-known/agent-card.json",
        headers={"Authorization": "Bearer verified"},
    )
    assert response.status_code == 200
    card = response.json()
    assert card["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
    }
    assert client.get("/.well-known/agent.json").status_code == 404
    assert auth.calls == 2


def test_json_rpc_unary_methods_and_required_idempotency_key() -> None:
    app, _auth = _app()
    client = TestClient(app)
    headers = {"Authorization": "Bearer verified"}
    missing = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {}},
        },
    )
    assert missing.json()["error"]["code"] == -32602

    for method, params in (
        ("message/send", {"message": {"kind": "message"}}),
        ("tasks/get", {"id": "task-1"}),
        ("tasks/list", {"limit": 10}),
        ("tasks/cancel", {"id": "task-1"}),
    ):
        response = client.post(
            "/a2a",
            headers={**headers, "Idempotency-Key": "key"},
            json={"jsonrpc": "2.0", "id": method, "method": method, "params": params},
        )
        assert response.status_code == 200
        assert "result" in response.json()
