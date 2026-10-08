"""GRAPHOS-HOST-R020: serving start installs the decide consumers, guarded."""

from __future__ import annotations

import asyncio

from agent_utilities.decide.consumers import assembly, task_planner
from agent_utilities.knowledge_graph.virtual_graph import federation

from graph_os.mcp_server import decide_wiring


class _Client:
    async def sparql(self, query: str) -> list:
        return []


def _patch(monkeypatch) -> dict:
    seen: dict = {}

    def fake_library(eg_client, session, engine, *, run):
        seen["assembler"] = (eg_client, session, engine, run)
        return "assembler"

    monkeypatch.setattr(assembly, "install_library_assembler", fake_library)
    monkeypatch.setattr(
        task_planner, "install_task_planner", lambda p: seen.setdefault("planner", p)
    )
    monkeypatch.setattr(
        federation,
        "install_cross_source",
        lambda catalog, provider: seen.setdefault("cross", (catalog, provider)),
    )
    return seen


def test_installs_all_three_consumers(monkeypatch) -> None:
    seen = _patch(monkeypatch)
    client = _Client()
    result = decide_wiring.install_decide_consumers(
        lambda s: client, "session", "engine", run=asyncio.run
    )
    assert result == {"assembler": True, "task_planner": True, "cross_source": True}
    assert seen["assembler"][:3] == (client, "session", "engine")
    assert seen["planner"].assembler == "assembler"
    assert seen["planner"].driver is asyncio.run
    catalog, provider = seen["cross"]
    assert catalog is not None and callable(provider)


def test_missing_client_skips_without_raising(monkeypatch) -> None:
    seen = _patch(monkeypatch)

    def broken(session):
        raise RuntimeError("no engine")

    result = decide_wiring.install_decide_consumers(broken, "session", "engine")
    assert not any(result.values())
    assert seen == {}


def test_assembler_fault_skips_planner_but_keeps_cross_source(monkeypatch) -> None:
    seen = _patch(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("layer unavailable")

    monkeypatch.setattr(assembly, "install_library_assembler", boom)
    result = decide_wiring.install_decide_consumers(
        lambda s: _Client(), "session", "engine"
    )
    assert result == {"assembler": False, "task_planner": False, "cross_source": True}
    assert "planner" not in seen


def test_client_without_sparql_skips_cross_source(monkeypatch) -> None:
    seen = _patch(monkeypatch)
    result = decide_wiring.install_decide_consumers(
        lambda s: object(), "session", "engine"
    )
    assert result["cross_source"] is False
    assert "cross" not in seen
