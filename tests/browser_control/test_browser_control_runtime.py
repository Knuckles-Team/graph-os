"""Same-instance and authorization tests for the browser-control caller adapter."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agent_utilities.knowledge_graph.core.session import (
    GraphSession,
    current_session,
    use_session,
)
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext

from graph_os.browser_control.browser_control_api import BrowserLeaseReceipt
from graph_os.browser_control.browser_control_runtime import (
    bind_browser_control_owner,
    dispatch_browser_control,
    register_browser_control_service,
    unregister_browser_control_service,
)


class _Service:
    def __init__(self) -> None:
        self.seen_session: GraphSession | None = None

    async def issue_lease(self, request: Any) -> BrowserLeaseReceipt:
        self.seen_session = current_session()
        return BrowserLeaseReceipt(
            lease_id="browserlease_0123456789abcdef0123456789abcdef",
            document_ref=request.document_ref,
            tool_ids=request.tool_ids,
            registration_generation=1,
            expires_at=100.0,
            hard_expires_at=200.0,
            status="active",
        )


@pytest.fixture
def session() -> GraphSession:
    actor = ActorContext(
        actor_id="browser-user",
        actor_type=ActorType.HUMAN,
        roles=("user",),
        tenant_id="tenant-a",
        authenticated=True,
    )
    return GraphSession(
        actor=actor,
        tenant="tenant-a",
        scopes=frozenset({"kg:write"}),
        policy_version="policy-7",
        audience="graph-runtime",
    )


@pytest.mark.asyncio
async def test_foreign_loop_dispatch_preserves_verified_session(
    session: GraphSession,
) -> None:
    service = _Service()
    owner_loop = asyncio.new_event_loop()
    ready = threading.Event()
    bound = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(owner_loop)
        owner_loop.call_soon(ready.set)
        owner_loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    register_browser_control_service(service)
    thread.start()
    assert await asyncio.to_thread(ready.wait, 1.0)

    def bind() -> None:
        bind_browser_control_owner(service)
        bound.set()

    owner_loop.call_soon_threadsafe(bind)
    assert await asyncio.to_thread(bound.wait, 1.0)
    try:
        with use_session(session):
            receipt = await dispatch_browser_control(
                "issue_lease",
                {
                    "document_ref": "document_" + "a" * 64,
                    "tool_ids": ["agent-webui.get-page-context"],
                    "attended": True,
                },
            )
        assert receipt.status == "active"
        assert service.seen_session == session
    finally:
        unregister_browser_control_service(service)
        owner_loop.call_soon_threadsafe(owner_loop.stop)
        await asyncio.to_thread(thread.join, 1.0)
        owner_loop.close()


@pytest.mark.asyncio
async def test_runtime_refuses_replacement_and_unbound_caller(
    session: GraphSession,
) -> None:
    first = _Service()
    second = _Service()
    register_browser_control_service(first)
    try:
        with pytest.raises(RuntimeError, match="different live"):
            register_browser_control_service(second)
        with use_session(session), pytest.raises(RuntimeError, match="not attached"):
            await dispatch_browser_control(
                "issue_lease",
                {
                    "document_ref": "document_" + "a" * 64,
                    "tool_ids": ["agent-webui.get-page-context"],
                    "attended": True,
                },
            )
    finally:
        unregister_browser_control_service(first)


@pytest.mark.asyncio
async def test_runtime_requires_write_scope(session: GraphSession) -> None:
    service = _Service()
    register_browser_control_service(service)
    bind_browser_control_owner(service)
    try:
        read_only = replace(session, scopes=frozenset({"kg:read"}))
        with use_session(read_only), pytest.raises(PermissionError):
            await dispatch_browser_control(
                "issue_lease",
                {
                    "document_ref": "document_" + "a" * 64,
                    "tool_ids": ["agent-webui.get-page-context"],
                    "attended": True,
                },
            )
    finally:
        unregister_browser_control_service(service)


def test_both_production_webui_compositions_inject_the_same_authority() -> None:
    from graph_os.webui_host import webui_co_service

    source = Path(webui_co_service.__file__).read_text(encoding="utf-8")
    assert "browser_control_factory_kwargs(" in source
    assert "**browser_control_kwargs" in source
    assert "register_browser_control_service(" in source
    assert "unregister_browser_control_service(" in source
