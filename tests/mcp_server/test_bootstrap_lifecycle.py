"""Native GraphOS bootstrap owns workers, never content ingestion."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

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
        "agent_utilities.knowledge_graph.core.engine_tasks._require_verified_background_session",
        lambda actual: actual,
    )

    def thread(_session: Any, target: Any, *, name: str) -> Any:
        assert name == "KGEngineBootstrap"
        return SimpleNamespace(start=target)

    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.core.engine_tasks._authorized_background_thread",
        thread,
    )
    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.core.engine_tasks.daemon_role",
        lambda: "host",
    )
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
