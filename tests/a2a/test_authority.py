"""Canonical WorkItem authority and fail-closed routing tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.a2a.authority import A2AIdempotencyConflict, WorkItemA2AAuthority
from graph_os.a2a.models import A2AMessage, A2ARouteDecision, A2ATextPart
from graph_os.a2a.routing import A2AAssemblyUnavailable, OrchestratorA2ARouter


def _message(text: str = "do work") -> A2AMessage:
    return A2AMessage(
        role="user",
        parts=[A2ATextPart(text=text)],
        message_id="message-1",
    )


@pytest.mark.asyncio
async def test_work_item_authority_reuses_one_store_for_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.knowledge_graph.core import work_durability as work

    rows: dict[str, dict[str, Any]] = {}
    enqueued: list[str] = []
    session = SimpleNamespace(
        tenant="tenant-a", actor=SimpleNamespace(actor_id="actor-a")
    )

    class Engine:
        _work_item_engine: Any

        def __init__(self) -> None:
            self._work_item_engine = self

        def query_cypher(self, _query: str, params: dict[str, Any]) -> list[Any]:
            return [
                value for key, value in sorted(rows.items()) if key > params["after"]
            ][: params["limit"]]

    engine = Engine()
    authority = WorkItemA2AAuthority(lambda: engine)
    monkeypatch.setattr(authority, "_session", lambda scope: session)
    monkeypatch.setattr(authority, "_prepare_task", lambda _engine, text: text)
    monkeypatch.setattr(
        "graph_os.a2a.authority.persistence_reference",
        lambda kind, value, **kwargs: f"{kind}-ref:{value}",
    )

    def submit(_engine: Any, **kwargs: Any) -> tuple[str, bool]:
        item_id = work.orchestrator_work_item_id(kwargs["task_id"])
        if item_id in rows:
            return item_id, False
        rows[item_id] = {
            "id": item_id,
            "kind": "orchestrator_task",
            "tenant": kwargs["session"].tenant,
            "created_by": kwargs["owner_ref"],
            "status": "ready",
            "updated_at": 1.0,
            "metadata": kwargs["metadata"],
        }
        return item_id, True

    monkeypatch.setattr(authority, "_submit", submit)
    monkeypatch.setattr(
        authority,
        "_enqueue",
        lambda _engine, **kwargs: enqueued.append(kwargs["task_id"]),
    )
    monkeypatch.setattr(
        work, "get_work_item", lambda _engine, item_id: rows.get(item_id)
    )

    def cancel(_engine: Any, item_id: str, **kwargs: Any) -> bool:
        rows[item_id]["status"] = "cancelled"
        rows[item_id]["updated_at"] = 2.0
        return True

    monkeypatch.setattr(work, "cancel_work_item", cancel)
    decision = A2ARouteDecision(agent_name="expert", selection_mode="test")
    first = await authority.dispatch(
        message=_message(), idempotency_key="same", decision=decision
    )
    replay = await authority.dispatch(
        message=_message(), idempotency_key="same", decision=decision
    )
    assert replay.id == first.id
    assert first.status.timestamp == "1970-01-01T00:00:01+00:00"
    assert enqueued == [first.id, first.id]

    loaded = await authority.get(first.id)
    listed, cursor = await authority.list(cursor=None, limit=10)
    cancelled = await authority.cancel(first.id)
    assert loaded == first
    assert listed == [first]
    assert cursor is None
    assert cancelled.status.state == "canceled"

    with pytest.raises(A2AIdempotencyConflict):
        await authority.dispatch(
            message=_message("different"),
            idempotency_key="same",
            decision=decision,
        )


@pytest.mark.asyncio
async def test_selected_tool_subset_is_refused_before_durable_admission() -> None:
    authority = WorkItemA2AAuthority(lambda: pytest.fail("engine was accessed"))
    with pytest.raises(A2AAssemblyUnavailable, match="cannot yet enforce"):
        await authority.dispatch(
            message=_message(),
            idempotency_key="key",
            decision=A2ARouteDecision(
                agent_name="expert",
                selected_tools=("graph_query",),
                selection_mode="agent-assemble",
            ),
        )


@pytest.mark.asyncio
async def test_context_budget_fails_closed_while_agent_assemble_is_unavailable() -> (
    None
):
    router = OrchestratorA2ARouter(lambda: pytest.fail("engine was accessed"))
    with pytest.raises(A2AAssemblyUnavailable, match="AgentAssemble"):
        await router.route(_message(), context_budget_tokens=4096)
