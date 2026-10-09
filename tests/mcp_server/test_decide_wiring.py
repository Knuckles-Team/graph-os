"""GRAPHOS-HOST-R020/R021/R022: serving start installs the decide consumers,
guarded, with bound commit/publish providers and task-planner lookups."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_utilities.decide.consumers import assembly, task_planner, topology
from agent_utilities.knowledge_graph.virtual_graph import federation

from graph_os.mcp_server import decide_wiring


class _Client:
    async def sparql(self, query: str) -> list:
        return []


def _patch(monkeypatch) -> dict:
    seen: dict = {}

    def fake_library(
        eg_client, session, engine, *, run, commit_context, publish_context
    ):
        seen["assembler"] = (eg_client, session, engine, run)
        seen["commit_context"] = commit_context
        seen["publish_context"] = publish_context
        return "assembler"

    monkeypatch.setattr(assembly, "install_library_assembler", fake_library)
    monkeypatch.setattr(
        topology,
        "install_topology",
        lambda a, r, t: seen.setdefault("topology", (a, r, t)),
    )
    monkeypatch.setattr(
        task_planner, "install_task_planner", lambda p: seen.setdefault("planner", p)
    )
    monkeypatch.setattr(
        federation,
        "install_cross_source",
        lambda catalog, provider: seen.setdefault("cross", (catalog, provider)),
    )
    return seen


def test_installs_all_four_consumers(monkeypatch) -> None:
    seen = _patch(monkeypatch)
    client = _Client()
    result = decide_wiring.install_decide_consumers(
        lambda s: client, "session", "engine", run=asyncio.run
    )
    assert result == {
        "assembler": True,
        "topology": True,
        "task_planner": True,
        "cross_source": True,
    }
    assert seen["assembler"][:3] == (client, "session", "engine")
    assert callable(seen["commit_context"])
    assert callable(seen["publish_context"])
    assert seen["topology"][0] == "assembler"
    assert seen["topology"][1] is asyncio.run
    assert seen["topology"][2]()  # non-empty published templates
    assert seen["planner"].assembler == "assembler"
    assert seen["planner"].driver is asyncio.run
    assert callable(seen["planner"].capability_search)
    assert callable(seen["planner"].guardrail_source)
    assert callable(seen["planner"].workflows)
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
    assert result == {
        "assembler": False,
        "topology": False,
        "task_planner": False,
        "cross_source": True,
    }
    assert "planner" not in seen
    assert "topology" not in seen


def test_client_without_sparql_skips_cross_source(monkeypatch) -> None:
    seen = _patch(monkeypatch)
    result = decide_wiring.install_decide_consumers(
        lambda s: object(), "session", "engine"
    )
    assert result["cross_source"] is False
    assert "cross" not in seen


def _session(**overrides: object) -> SimpleNamespace:
    base = dict(
        actor=SimpleNamespace(actor_id="actor-1"),
        tenant="tenant-x",
        policy_version="policy-v1",
        trace_context="trace-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeGraphs:
    """Records every ``commit_decision``/``publish_graph`` call it serves."""

    def __init__(self) -> None:
        self.committed: list[dict] = []
        self.published: list[tuple] = []

    async def assemble(self, request: dict) -> dict:
        return {
            "record": {"outcome": {"outcome": "solved", "reasons": []}},
            "agents": [{"agent_id": "a1"}],
            "graph": {"graph_id": "g1"},
        }

    async def commit_decision(self, request: dict, *, idempotency_key=None) -> dict:
        self.committed.append(request)
        return {
            "record_id": "rec-1",
            "component": {
                "component": {
                    "kind": "decision_record",
                    "definition_digest": "sha256:deadbeef",
                }
            },
        }

    async def publish_graph(
        self, draft, context, *, evidence=None, idempotency_key=None
    ):
        self.published.append((draft, context, evidence))
        return {"graph_id": "g1"}


def test_solved_assembly_is_committed_and_published() -> None:
    """GRAPHOS-HOST-R021: a solved assembly commits through the bound engine
    client, with the graph-os-minted mutation context (fake engine records
    the call)."""
    session = _session()
    graphs = _FakeGraphs()
    assembler = assembly.Assembler(
        graphs,
        "tenant-x",
        commit_context=decide_wiring._commit_context(session),
        publish_context=decide_wiring._publish_context(session),
    )

    answer = asyncio.run(
        assembler.assemble({"tenant_id": "tenant-x"}, lambda reasons: None)
    )

    assert answer.committed is not None
    assert len(graphs.committed) == 1
    assert graphs.committed[0]["tenant_id"] == "tenant-x"
    assert graphs.committed[0]["actor_scope"] == "actor-1"
    assert graphs.committed[0]["purpose_id"] == "agent_library.assembly_commit"
    assert len(graphs.published) == 1
    _, publish_context, _ = graphs.published[0]
    assert publish_context["purpose_id"] == "agent_library.assembly_publish"


def test_unsolved_assembly_is_never_committed() -> None:
    session = _session()

    class _AbstainingGraphs(_FakeGraphs):
        async def assemble(self, request: dict) -> dict:
            return {"record": {"outcome": {"outcome": "abstained", "reasons": []}}}

    graphs = _AbstainingGraphs()
    assembler = assembly.Assembler(
        graphs,
        "tenant-x",
        commit_context=decide_wiring._commit_context(session),
        publish_context=decide_wiring._publish_context(session),
    )

    answer = asyncio.run(
        assembler.assemble({"tenant_id": "tenant-x"}, lambda reasons: None)
    )

    assert answer.committed is None
    assert graphs.committed == []
    assert graphs.published == []


class _FakeAssembler:
    """A minimal assembler stand-in: ``assemble_mapped`` only, no topology."""

    tenant = "tenant-x"

    async def assemble_mapped(self, goal: str, mapped: list[str]):
        return assembly.Assembled(
            result={
                "record": {"outcome": {"outcome": "solved", "reasons": []}},
                "agents": [
                    {
                        "agent_id": "agent-1",
                        "tools": [{"component_id": "tool-1"}],
                        "skills": [{"component_id": "skill-1"}],
                        "model_identity": "model-1",
                        "system_prompt": {"component_id": "prompt-1"},
                    }
                ],
            },
            reason="solved",
        )


def test_planned_task_gets_nonempty_skills_and_tools_from_fake_capability_source() -> (
    None
):
    """GRAPHOS-HOST-R022: the task planner's reuse lookup feeds plan.agents
    from a fake capability source, while the assembled components still
    carry the agent's non-empty skills and tools."""

    async def fake_capability_search(task_iris):
        return [{"kind": "a2a_agent", "id": "agent-xyz"}]

    planner = task_planner.TaskPlanner(
        assembler=_FakeAssembler(),
        templates=None,
        capability_search=fake_capability_search,
    )

    plan = asyncio.run(planner.plan("implement the thing"))

    assert plan.agents, "expected at least one planned agent"
    agent = plan.agents[0]
    assert agent["skills"] == ["skill-1"]
    assert agent["tools"] == ["tool-1"]
    assert agent["reuses"] == "agent-xyz"
    assert agent["source"] == "a2a"
