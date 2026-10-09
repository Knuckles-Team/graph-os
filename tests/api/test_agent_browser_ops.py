"""Focused contracts for the composed agent and browser operation tables."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from graph_os.api.ops import agents, browser
from graph_os.api.registry import Effect, PrincipalRule, Registry, Surface


def _context(**services: object) -> SimpleNamespace:
    return SimpleNamespace(
        caller=SimpleNamespace(session=object()),
        client=object(),
        idempotency_key="key-1",
        services=services,
    )


@pytest.mark.spec("GRAPHOS-FLEET-R015", "GRAPHOS-FLEET-R018", "GRAPHOS-OPS-R023")
def test_agent_and_browser_ops_have_unique_ids_and_surface_limits() -> None:
    registry = Registry((*agents.operations(), *browser.operations()))
    assert len(registry) == 12
    assert registry["agents.run"].effect is Effect.WRITE
    assert (
        registry["browser.execute_call"].principals is PrincipalRule.HUMAN_UNDELEGATED
    )
    assert registry["browser.execute_call"].surfaces == frozenset(
        {Surface.HTTP, Surface.CONSOLE}
    )
    assert all(op.examples for op in registry)


@pytest.mark.spec("GRAPHOS-FLEET-R015", "GRAPHOS-FLEET-R018", "GRAPHOS-OPS-R023")
@pytest.mark.asyncio
async def test_agent_run_uses_the_existing_a2a_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_utilities.api

    monkeypatch.setattr(
        agent_utilities.api, "use_session", lambda _session: nullcontext()
    )
    calls: list[dict[str, object]] = []

    class Service:
        async def send_message(self, **params: object) -> SimpleNamespace:
            calls.append(params)
            return SimpleNamespace(model_dump=lambda **_kwargs: {"id": "task-1"})

    op = next(op for op in agents.operations() if op.id == "agents.run")
    result = await agents.handle_agent(
        _context(a2a=Service()),
        {"message": {"parts": []}, "context_budget_tokens": None},
        op,
    )
    assert result == {"value": {"id": "task-1"}}
    assert calls == [
        {
            "message": {"parts": []},
            "idempotency_key": "key-1",
            "context_budget_tokens": None,
        }
    ]


@pytest.mark.spec("GRAPHOS-FLEET-R015", "GRAPHOS-FLEET-R018", "GRAPHOS-OPS-R023")
@pytest.mark.asyncio
async def test_browser_op_uses_the_registered_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_utilities.api

    monkeypatch.setattr(
        agent_utilities.api, "use_session", lambda _session: nullcontext()
    )
    called: list[tuple[str, dict[str, object]]] = []

    async def dispatch(
        action: str, payload: dict[str, object], caller: object
    ) -> SimpleNamespace:
        called.append((action, payload))
        return SimpleNamespace(model_dump=lambda **_kwargs: {"status": "accepted"})

    op = next(op for op in browser.operations() if op.id == "browser.execute_call")
    result = await browser.handle_browser(
        _context(browser_control=dispatch), {"payload": {"call_id": "c1"}}, op
    )
    assert result == {"value": {"status": "accepted"}}
    assert called == [("execute_call", {"call_id": "c1"})]
