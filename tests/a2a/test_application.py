"""A2A JSON-RPC transport contracts: unary methods and SSE streaming."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient

from graph_os.a2a.application import create_a2a_application
from graph_os.a2a.authority import A2AIdempotencyConflict
from graph_os.a2a.models import (
    A2ARouteDecision,
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
)
from graph_os.a2a.op_invoke import OperationReply
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

    async def output(self, task_id: str) -> str | None:
        return "the answer" if task_id == self.task.id else None


def _app() -> tuple[Any, Authenticator, Authority]:
    auth = Authenticator()
    authority = Authority()
    app = create_a2a_application(
        service=A2AService(authority=authority, router=Router()),
        authenticator=auth,
    )
    return app, auth, authority


def test_native_protocol_routes_keep_methods_and_openapi_paths() -> None:
    app, _auth, _authority = _app()
    expected = {
        "/.well-known/agent-card.json": {"GET"},
        "/a2a": {"POST"},
    }
    assert {
        route.path: route.methods for route in app.routes if route.path in expected
    } == expected
    assert {path: set(app.openapi()["paths"][path]) for path in expected} == {
        path: {method.lower() for method in methods}
        for path, methods in expected.items()
    }


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
        "streaming": True,
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


def test_operation_extension_uses_verified_authenticator_and_projection() -> None:
    class Projection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def invoke(self, method: str, params: dict[str, Any]) -> OperationReply:
            self.calls.append((method, params))
            return OperationReply(value={"state": "input-required", "plan_ref": "p1"})

    auth = Authenticator()
    projection = Projection()
    app = create_a2a_application(
        service=A2AService(authority=Authority(), router=Router()),
        authenticator=auth,
        operation_projection=projection,
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "graphos.op/invoke",
        "params": {"op": "query.uql", "params": {"query": "a"}},
    }
    client = TestClient(app)
    assert client.post("/a2a", json=payload).status_code == 401
    answer = client.post(
        "/a2a", json=payload, headers={"Authorization": "Bearer verified"}
    )
    assert answer.status_code == 200
    assert answer.json()["result"] == {"state": "input-required", "plan_ref": "p1"}
    assert projection.calls == [("graphos.op/invoke", payload["params"])]
    assert auth.scopes == ["", ""]


def test_operation_extension_is_not_advertised_without_composed_projection() -> None:
    app, _auth, _authority = _app()
    answer = TestClient(app).post(
        "/a2a",
        headers={"Authorization": "Bearer verified"},
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "graphos.plan/confirm",
            "params": {"plan_ref": "p1", "op": "query.uql", "params": {}},
        },
    )
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == -32601


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


class ProgressAuthority(Authority):
    """Walks submitted -> working -> completed across successive reads."""

    def __init__(self) -> None:
        super().__init__()
        self.states: list[A2ATaskState] = ["working", "working", "completed"]

    async def get(self, task_id: str) -> A2ATask | None:
        if task_id != self.task.id:
            return None
        if self.states:
            state = self.states.pop(0)
            self.task = self.task.model_copy(
                update={"status": A2ATaskStatus(state=state, timestamp=state)}
            )
        return self.task


async def _no_sleep(_seconds: float) -> None:
    return None


def _stream_app(authority: Authority) -> TestClient:
    from graph_os.a2a.service import StreamPolicy

    service = A2AService(
        authority=authority,
        router=Router(),
        stream_policy=StreamPolicy(sleep=_no_sleep),
    )
    return TestClient(
        create_a2a_application(service=service, authenticator=Authenticator())
    )


def _frames(body: str) -> list[dict[str, Any]]:
    import json

    frames = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frame = json.loads(fields["data"])
        frame["_id"] = fields.get("id")
        frames.append(frame)
    return frames


_STREAM_MESSAGE = {
    "role": "user",
    "parts": [{"kind": "text", "text": "do work"}],
    "messageId": "message-1",
}


def test_message_stream_follows_the_task_to_its_final_state() -> None:
    client = _stream_app(ProgressAuthority())

    response = client.post(
        "/a2a",
        headers={"Authorization": "Bearer verified", "Idempotency-Key": "send-key"},
        json={
            "jsonrpc": "2.0",
            "id": "s",
            "method": "message/stream",
            "params": {"message": _STREAM_MESSAGE},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    kinds = [frame["result"]["kind"] for frame in frames]
    assert kinds == ["task", "status-update", "artifact-update", "status-update"]
    answer = frames[2]["result"]["artifact"]["parts"][0]["text"]
    assert answer == "the answer"
    states = [
        frame["result"]["status"]["state"]
        for frame in frames
        if frame["result"]["kind"] != "artifact-update"
    ]
    assert states == ["submitted", "working", "completed"]
    assert [frame["result"].get("final") for frame in frames[-2:]] == [None, True]
    assert all(frame["_id"] for frame in frames)


def test_stream_requires_authentication_and_idempotency() -> None:
    client = _stream_app(ProgressAuthority())
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/stream",
        "params": {"message": _STREAM_MESSAGE},
    }

    assert client.post("/a2a", json=body).status_code == 401
    missing_key = client.post(
        "/a2a", headers={"Authorization": "Bearer verified"}, json=body
    )
    assert missing_key.status_code == 400


def test_resubscribe_skips_the_state_the_client_already_saw() -> None:
    authority = ProgressAuthority()
    authority.states = ["completed"]
    client = _stream_app(authority)
    headers = {"Authorization": "Bearer verified"}
    body = {
        "jsonrpc": "2.0",
        "id": "r",
        "method": "tasks/resubscribe",
        "params": {"id": authority.task.id},
    }

    first = _frames(client.post("/a2a", headers=headers, json=body).text)
    assert [frame["result"]["kind"] for frame in first] == [
        "artifact-update",
        "status-update",
    ]
    assert first[-1]["result"]["final"] is True
    seen = first[-1]["_id"]
    again = client.post("/a2a", headers={**headers, "Last-Event-ID": seen}, json=body)
    assert again.text == ""


def test_stream_stops_at_input_required_for_client_answer() -> None:
    authority = ProgressAuthority()
    authority.states = ["input-required"]
    client = _stream_app(authority)
    response = client.post(
        "/a2a",
        headers={
            "Authorization": "Bearer verified",
            "Idempotency-Key": "await-approval",
        },
        json={
            "jsonrpc": "2.0",
            "id": "await",
            "method": "message/stream",
            "params": {"message": _STREAM_MESSAGE},
        },
    )
    frames = _frames(response.text)
    assert frames[-1]["result"]["status"]["state"] == "input-required"
    assert frames[-1]["result"]["final"] is False


def test_resubscribe_to_an_unknown_task_reports_not_found() -> None:
    client = _stream_app(ProgressAuthority())
    response = client.post(
        "/a2a",
        headers={"Authorization": "Bearer verified"},
        json={
            "jsonrpc": "2.0",
            "id": "x",
            "method": "tasks/resubscribe",
            "params": {"id": "a2a-" + "9" * 64},
        },
    )
    frames = _frames(response.text)
    assert frames[0]["error"]["code"] == -32001
