"""Focused contract checks for work and evolution operation declarations."""

from types import SimpleNamespace

import pytest

from graph_os.api.ops import evolution, work
from graph_os.api.registry import Effect, EgMethod
from graph_os.gateway import daemon


def test_work_item_methods_match_eg_authority() -> None:
    specs = {op.id: op for op in work.specs()}
    assert isinstance(specs["work.items.submit"].binding, EgMethod)
    assert specs["work.items.submit"].scopes == {"work:submit"}
    assert specs["work.items.list"].scopes == {"work:read"}
    assert specs["work.items.cancel"].effect is Effect.DESTRUCTIVE
    assert specs["work.items.cancel"].scopes == {"work:write"}


def test_evolution_controls_have_exact_scopes() -> None:
    specs = {op.id: op for op in evolution.specs()}
    assert specs["evolution.loops.status"].scopes == {"loops:read"}
    assert specs["evolution.loops.run"].scopes == {"loops:control"}
    assert specs["evolution.loops.pause"].scopes == {"loops:control"}
    assert specs["evolution.loops.pause"].effect is Effect.ADMIN


@pytest.mark.asyncio
async def test_market_fails_closed_without_composed_port() -> None:
    op = next(op for op in work.specs() if op.id == "work.gaps.list")
    with pytest.raises(RuntimeError, match="unavailable"):
        await work.market_handler(SimpleNamespace(services={}), {}, op)


@pytest.mark.asyncio
async def test_evolution_fails_closed_without_composed_port() -> None:
    op = next(op for op in evolution.specs() if op.id == "evolution.loops.run")
    with pytest.raises(RuntimeError, match="unavailable"):
        await evolution.evolution_handler(SimpleNamespace(services={}), {}, op)


@pytest.mark.asyncio
async def test_daemon_control_dispatches_only_supported_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        daemon, "evolution_loop_status", lambda *, limit: {"limit": limit}
    )
    control = daemon.EvolutionDaemonControl()
    assert await control.execute("evolution.loops.status", {"limit": 3}) == {"limit": 3}
    with pytest.raises(RuntimeError, match="unavailable"):
        await control.execute("evolution.proposals.review", {})


def test_pause_refuses_unrelated_schedule() -> None:
    with pytest.raises(ValueError, match="only the loop"):
        daemon.evolution_loop_pause(schedule_name="security")
