"""Unary A2A transport contracts."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from graph_os.a2a.application import create_a2a_application
from graph_os.a2a.authority import A2AIdempotencyConflict
from graph_os.a2a.models import A2ARouteDecision, A2ATask, A2ATaskStatus
from graph_os.a2a.service import A2AService


class Authenticator:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def authenticate(self, request: Any, *, scope: str) -> None:
        self.scopes.append(scope)
        if request.headers.get("Authorization") != "Bearer verified":
            raise HTTPException(status_code=401)


class Router:
    async def route(self, message: Any, *, context_budget_tokens: int | None) -> Any:
        assert message.task_text() == "do work"
        assert context_budget_tokens is None
        return A2ARouteDecision(
            agent_name="agent-utilities-expert", selection_mode="test"
        )


class Authority:
    def __init__(self) -> None:
        self.task = A2ATask(
            id="a2a-" + "1" * 64,
            context_id="a2a-context-" + "2" * 64,
            status=A2ATaskStatus(state="submitted"),
        )

    async def dispatch(self, **kwargs: Any) -> A2ATask:
        assert kwargs["idempotency_key"] == "send-key"
        return self.task

    async def get(self, task_id: str) -> A2ATask | None:
        return self.task if task_id == self.task.id else None

    async def list(self, **kwargs: Any) -> tuple[list[A2ATask], str | None]:
        return [self.task], "next"

    async def cancel(self, task_id: str) -> A2ATask:
        return self.task.model_copy(update={"status": A2ATaskStatus(state="canceled")})


def _app() -> tuple[Any, Authenticator, Authority]:
    auth = Authenticator()
    authority = Authority()
    app = create_a2a_application(
        service=A2AService(authority=authority, router=Router()),
        authenticator=auth,
    )
    return app, auth, authority


def test_agent_card_is_authenticated_truthful_and_has_one_path() -> None:
    app, auth, _authority = _app()
    client = TestClient(app)
    assert client.get("/.well-known/agent-card.json").status_code == 401
    response = client.get(
        "/.well-known/agent-card.json",
        headers={"Authorization": "Bearer verified"},
    )
    assert response.status_code == 200
    card = response.json()
    assert card["url"] == "http://testserver/a2a"
    assert card["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    }
    assert card["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert client.get("/.well-known/agent.json").status_code == 404
    assert auth.scopes == ["kg:read", "kg:read"]


def test_json_rpc_unary_methods_use_typed_shared_contract() -> None:
    app, auth, authority = _app()
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer verified",
        "Idempotency-Key": "send-key",
    }
    message = {
        "role": "user",
        "parts": [{"kind": "text", "text": "do work"}],
        "messageId": "message-1",
    }
    calls = (
        ("message/send", {"message": message}),
        ("tasks/get", {"id": authority.task.id}),
        ("tasks/list", {"limit": 10}),
        ("tasks/cancel", {"id": authority.task.id}),
    )
    for method, params in calls:
        response = client.post(
            "/a2a",
            headers=headers,
            json={"jsonrpc": "2.0", "id": method, "method": method, "params": params},
        )
        assert response.status_code == 200
        assert "result" in response.json()
    assert auth.scopes == ["kg:write", "kg:read", "kg:read", "kg:write"]


def test_json_rpc_rejects_missing_idempotency_invalid_shape_and_unknown_task() -> None:
    app, _auth, _authority = _app()
    client = TestClient(app)
    headers = {"Authorization": "Bearer verified"}
    invalid = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": []}},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == -32602

    missing = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tasks/get",
            "params": {"id": "a2a-" + "9" * 64},
        },
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == -32001

    unknown = client.post(
        "/a2a",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 3, "method": "tasks/unknown", "params": {}},
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == -32601


@pytest.mark.spec("GRAPHOS-A2A-R001.1")
def test_json_rpc_refuses_resubscribe_until_streaming_is_implemented() -> None:
    """GRAPHOS-A2A-R001.1: resubscribe is on the method table but fail-closed.

    Durable streaming has no bounded, restart-safe event-cursor backing yet
    (GRAPHOS-A2A-R001), so ``tasks/resubscribe`` must refuse distinctly from
    an unrecognized method, and must still validate its params.
    """
    app, auth, authority = _app()
    client = TestClient(app)
    headers = {"Authorization": "Bearer verified"}

    refused = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "resubscribe",
            "method": "tasks/resubscribe",
            "params": {"id": authority.task.id, "cursor": None},
        },
    )
    assert refused.status_code == 501
    assert refused.json()["error"] == {
        "code": -32010,
        "message": "durable task streaming and resubscribe are not available yet",
    }
    assert auth.scopes == ["kg:read"]

    invalid = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "resubscribe-invalid",
            "method": "tasks/resubscribe",
            "params": {},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == -32602


@pytest.mark.spec("GRAPHOS-A2A-R007", "GRAPHOS-ACP-R001")
def test_json_rpc_preserves_service_error_translation() -> None:
    class ConflictAuthority(Authority):
        async def dispatch(self, **kwargs: Any) -> A2ATask:
            raise A2AIdempotencyConflict("idempotency conflict")

    app = create_a2a_application(
        service=A2AService(authority=ConflictAuthority(), router=Router()),
        authenticator=Authenticator(),
    )
    response = TestClient(app).post(
        "/a2a",
        headers={
            "Authorization": "Bearer verified",
            "Idempotency-Key": "send-key",
        },
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "do work"}],
                    "messageId": "message-1",
                }
            },
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": -32009,
        "message": "idempotency conflict",
    }


@pytest.mark.spec("GRAPHOS-A2A-R007", "GRAPHOS-ACP-R001")
def test_json_rpc_refuses_op_invoke_until_registry_bridge_exists() -> None:
    """GRAPHOS-A2A-R006.1: ``graphos.op/invoke`` is on the method table but
    fail-closed until it is bridged to the shared hosted-operation registry
    (``Surface.A2A``), with a distinct error code, not method-not-found.
    """
    app, auth, _authority = _app()
    client = TestClient(app)
    headers = {"Authorization": "Bearer verified"}

    refused = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "invoke",
            "method": "graphos.op/invoke",
            "params": {"op": "kg.search", "params": {}},
        },
    )
    assert refused.status_code == 501
    assert refused.json()["error"]["code"] == -32011
    assert auth.scopes == ["kg:read"]

    invalid = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "invoke-invalid",
            "method": "graphos.op/invoke",
            "params": {},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == -32602


@pytest.mark.spec("GRAPHOS-A2A-R007", "GRAPHOS-ACP-R001")
def test_json_rpc_refuses_plan_confirm_until_approval_exchange_exists() -> None:
    """GRAPHOS-A2A-R005.1/R006.1: the human approval response method is
    registered but every confirmation attempt is rejected until the full
    signed exchange is implemented end to end.
    """
    app, auth, authority = _app()
    client = TestClient(app)
    headers = {"Authorization": "Bearer verified"}

    refused = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "confirm",
            "method": "graphos.plan/confirm",
            "params": {
                "task_id": authority.task.id,
                "pending_call_id": "call-1",
                "plan_ref": "plan-1",
                "signature": "sig",
            },
        },
    )
    assert refused.status_code == 501
    assert refused.json()["error"]["code"] == -32011
    assert auth.scopes == ["kg:write"]

    invalid = client.post(
        "/a2a",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "confirm-invalid",
            "method": "graphos.plan/confirm",
            "params": {},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == -32602
