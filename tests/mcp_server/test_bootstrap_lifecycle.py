"""Native GraphOS bootstrap owns workers, never content ingestion."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.mcp_server import bootstrap


def test_bootstrap_starts_workers_after_materialization_without_hydration(
    monkeypatch,
) -> None:
    events: list[str] = []
    engine = SimpleNamespace(
        _daemon_role="host",
        _effective_role="host",
        backend=SimpleNamespace(read_only=False),
        start_background_daemons=lambda: events.append("daemons"),
        start_task_workers=lambda: events.append("workers"),
    )
    session = SimpleNamespace(actor=SimpleNamespace(actor_id="graph-os-host"))

    monkeypatch.setattr(bootstrap, "_get_engine", lambda: engine)
    monkeypatch.setattr(
        bootstrap,
        "_wait_for_engine_materialization",
        lambda actual: events.append("materialized") if actual is engine else None,
    )
    monkeypatch.setattr(
        bootstrap, "_require_verified_background_session", lambda actual: actual
    )

    def thread(_session: Any, target: Any, *, name: str) -> Any:
        assert name == "KGEngineBootstrap"
        return SimpleNamespace(start=target)

    monkeypatch.setattr(bootstrap, "_authorized_background_thread", thread)
    monkeypatch.setattr(bootstrap, "daemon_role", lambda: "host")
    monkeypatch.setattr(
        "agent_utilities.api.session.use_session",
        lambda _session: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "agent_utilities.security.brain_context.use_actor",
        lambda _actor: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "agent_utilities.security.system_rbac_admission.ensure_system_principal_access",
        lambda _actor_id: None,
    )

    bootstrap._start_engine_bootstrap(session)

    assert events == ["materialized", "daemons", "workers"]


def test_background_thread_restores_verified_authority(monkeypatch) -> None:
    from agent_utilities.api import GraphSession
    from agent_utilities.security.actor_identity import ActorType
    from agent_utilities.security.brain_context import ActorContext, current_actor

    actor = ActorContext(
        actor_id="graph-os-host",
        actor_type=ActorType.AUTOMATED_SERVICE,
        tenant_id="t1",
        authenticated=True,
    )
    session = GraphSession(
        actor=actor,
        tenant="t1",
        graph="t1",
        scopes=frozenset({"kg:read"}),
        policy_version="test",
        audience="test",
    )
    monkeypatch.setattr(
        GraphSession, "engine_verified_context", lambda self: {"tenant": "t1"}
    )
    seen: list[Any] = []

    worker = bootstrap._authorized_background_thread(
        session, lambda: seen.append(current_actor()), name="probe"
    )
    worker.start()
    worker.join(5)

    assert seen == [actor]
    assert worker.daemon is True


def test_background_thread_refuses_unverified_authority() -> None:
    from agent_utilities.api import SessionRequiredError

    with pytest.raises(SessionRequiredError):
        bootstrap._authorized_background_thread(object(), lambda: None, name="x")


def test_daemon_role_rejects_unknown_values(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "setting", lambda key, default=None: "Leader")
    assert bootstrap.daemon_role() == "auto"
    monkeypatch.setattr(bootstrap, "setting", lambda key, default=None: " Client ")
    assert bootstrap.daemon_role() == "client"
