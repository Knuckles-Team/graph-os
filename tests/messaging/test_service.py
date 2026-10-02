"""GraphOS messaging service governance and channel routing tests (CONCEPT:AU-ECO.messaging.messaging-reach-service-governed–4.52).

Covers governed sends, last-active channel routing + default fallback, the awaited-reply
bridge, and that the inbound planner handler is no longer the canned-acknowledgment stub.
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_utilities.messaging.models import (
    EventType,
    InboundEvent,
    SendResult,
)

from graph_os.messaging import service as service_module
from graph_os.messaging.service import MessagingService

from ._fakes import FakeMessagingBackend as _FakeBackend


def test_inbound_inbox_uses_only_bound_host_engine() -> None:
    class Engine:
        def __init__(self) -> None:
            self.nodes: dict[str, dict[str, object]] = {}

        def add_node(
            self, *, node_id: str, node_type: str, properties: dict[str, object]
        ) -> None:
            self.nodes[node_id] = properties

    bound = Engine()
    other = Engine()
    service = service_module.MessagingService(bound)
    with pytest.raises(PermissionError, match="bound host engine"):
        service.persist_inbound(
            other,
            platform="telegram",
            channel_id="42",
            message_id="1",
            text="hello",
            session="messaging:telegram:42",
        )
    inbox_id = service.persist_inbound(
        bound,
        platform="telegram",
        channel_id="42",
        message_id="1",
        text="hello",
        session="messaging:telegram:42",
    )
    assert inbox_id in bound.nodes
    assert not other.nodes
    with pytest.raises(PermissionError, match="bound host engine"):
        service.mark_inbound_answered(other, inbox_id)
    service.mark_inbound_answered(bound, inbox_id)
    assert bound.nodes[inbox_id]["status"] == "answered"


async def _immediate_policy(operation: Any) -> Any:
    return operation()


class _FakeEngine:
    """In-memory engine stub for add_node / query_cypher / recall_memory."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.memories: list[dict[str, Any]] = []

    def add_node(self, node_id: str, _label: str, properties: dict[str, Any]) -> None:
        self.nodes[node_id] = dict(properties)

    def query_cypher(self, _query: str, params: dict[str, Any]):
        node = self.nodes.get(params.get("id", ""))
        return [{"p": {"properties": node}}] if node else []

    def recall_memory(self, **_: Any):
        return []

    def store_memory(self, **kwargs: Any):
        self.memories.append(kwargs)
        return "mem-1"


@pytest.fixture()
def svc(monkeypatch: pytest.MonkeyPatch) -> MessagingService:
    """Fresh service bound to a fake engine + backend, with the policy gate allowing."""
    MessagingService._instance = None
    service = MessagingService.instance(_FakeEngine())
    backend = _FakeBackend()
    service.register_connected(backend)

    async def _get_backend(_platform: str):
        return backend

    monkeypatch.setattr(service, "get_backend", _get_backend)
    # Isolate routing from the policy engine — gate allows.
    monkeypatch.setattr(
        service, "_gate", lambda *a, **k: type("D", (), {"allowed": True})()
    )
    monkeypatch.setattr(
        service_module,
        "run_action_policy",
        lambda operation, *, policy_runner: _immediate_policy(operation),
    )
    return service


@pytest.mark.asyncio
async def test_reach_user_uses_last_active_channel(svc: MessagingService) -> None:
    # No pref yet, no default → cannot route.
    res = await svc.reach_user("hi", user_id="u1")
    assert not res.success

    # An inbound event records the last-active channel (ECO-4.49)…
    svc.record_inbound(
        InboundEvent(
            event_type=EventType.MESSAGE,
            platform="telegram",
            channel_id="555",
            user_id="u1",
        )
    )
    # …so reach_user now routes there.
    res = await svc.reach_user("hello", user_id="u1")
    assert res.success
    backend = await svc.get_backend("telegram")
    assert backend is not None
    assert backend.sent[-1] == ("555", "hello")


@pytest.mark.asyncio
async def test_reach_user_falls_back_to_default_channel(
    svc: MessagingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MESSAGING_DEFAULT_PLATFORM", "telegram")
    monkeypatch.setenv("MESSAGING_DEFAULT_CHANNEL", "999")
    res = await svc.reach_user("yo", user_id="unknown-user")
    assert res.success
    backend = await svc.get_backend("telegram")
    assert backend is not None
    assert backend.sent[-1] == ("999", "yo")


@pytest.mark.asyncio
async def test_send_blocked_when_policy_denies(
    svc: MessagingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc,
        "_gate",
        lambda *a, **k: type(
            "D", (), {"allowed": False, "decision": "deny", "reason": "x"}
        )(),
    )
    res = await svc.send("telegram", "1", "blocked?")
    assert not res.success
    assert "policy" in res.error


@pytest.mark.asyncio
async def test_send_refuses_when_action_policy_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken authorization dependency cannot deliver or persist a message."""
    from agent_utilities.orchestration import action_policy

    engine = _FakeEngine()
    service = MessagingService(engine)
    backend = _FakeBackend()
    service.register_connected(backend)

    def _raise_policy_error(_engine: Any) -> None:
        raise RuntimeError("sensitive policy backend detail")

    monkeypatch.setattr(action_policy, "get_action_policy", _raise_policy_error)

    result = await service.send(
        "telegram", "1", "must not leave the process", policy_runner=_immediate_policy
    )

    assert result == SendResult(
        success=False,
        platform="telegram",
        channel_id="1",
        error="action policy unavailable",
    )
    assert "sensitive" not in result.model_dump_json()
    assert "sensitive" not in caplog.text
    assert backend.sent == []
    assert engine.memories == []


@pytest.mark.asyncio
async def test_send_can_suppress_outbound_kg_persistence(
    svc: MessagingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contact delivery can send PII without copying it into KG memory."""
    ingest = AsyncMock()
    monkeypatch.setattr(svc, "_ingest_outbound", ingest)

    result = await svc.send(
        "telegram",
        "support",
        "contact form PII",
        persist_outbound=False,
    )

    assert result.success is True
    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_backend_connect_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.messaging.registry import MessagingRegistry

    class _BrokenBackend:
        async def connect(self) -> None:
            raise RuntimeError("provider leaked secret detail")

    registry = type(
        "Registry",
        (),
        {
            "is_installed": lambda self, _platform: True,
            "create_backend": lambda self, _platform: _BrokenBackend(),
        },
    )()
    monkeypatch.setattr(MessagingRegistry, "instance", lambda: registry)
    logged: list[str] = []
    monkeypatch.setattr(
        service_module.logger,
        "warning",
        lambda message, *args: logged.append(message % args),
    )
    service = MessagingService(_FakeEngine())
    monkeypatch.setattr(
        service, "_gate", lambda *a, **k: type("D", (), {"allowed": True})()
    )

    result = await service.send(
        "telegram", "support", "contact form PII", policy_runner=_immediate_policy
    )

    assert result.success is False
    diagnostics = "\n".join(logged)
    assert "secret detail" not in diagnostics
    assert "RuntimeError" in diagnostics


@pytest.mark.asyncio
async def test_deliver_reply_resolves_awaited_reply(svc: MessagingService) -> None:
    svc.record_inbound(
        InboundEvent(
            event_type=EventType.MESSAGE,
            platform="telegram",
            channel_id="42",
            user_id="u1",
        )
    )

    async def _answer_later() -> None:
        await asyncio.sleep(0.05)
        assert svc.deliver_reply("telegram", "42", "the answer")

    asyncio.create_task(_answer_later())
    reply = await svc.reach_user_and_wait("question?", user_id="u1", timeout=5.0)
    assert reply == "the answer"


def test_registry_identity_digest_uses_stable_bot_id_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.messaging.registry import MessagingRegistry

    from ._fakes import synthetic_token

    registry = MessagingRegistry.__new__(MessagingRegistry)
    monkeypatch.setattr(
        registry,
        "_auto_config",
        lambda _platform: SimpleNamespace(
            token=synthetic_token("123456", ":", "secret-can-rotate"),
            app_id="",
            webhook_url="",
        ),
    )
    digest = registry.identity_digest("telegram")
    assert digest == hashlib.sha256(b"telegram:123456").hexdigest()
    assert "secret" not in digest


def test_graphos_backend_status_reads_only_connected_configured_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.messaging.registry import MessagingRegistry

    registry = SimpleNamespace(configured_backend_ids=lambda: ["telegram", "slack"])
    monkeypatch.setattr(MessagingRegistry, "instance", lambda: registry)
    service = MessagingService(object())
    service._backends["telegram"] = _FakeBackend()
    assert service.backend_status() == {
        "configured": ["telegram", "slack"],
        "connected": ["telegram"],
    }
