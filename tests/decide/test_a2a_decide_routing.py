"""EH-044: A2A routing through EG assembly first; publish the routed graph."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.a2a.decide_routing import CAPABILITY_IRIS_METADATA_KEY, DecideA2ARouter
from graph_os.a2a.models import A2AMessage, A2ARouteDecision, A2ATextPart
from graph_os.a2a.routing import TASK_IRIS_METADATA_KEY, A2AAssemblyUnavailable

from .fakes import RECORD_ID, abstained, composition, solved

CURRENT = A2ARouteDecision(agent_name="current-agent", selection_mode="current")


class _Inner:
    def __init__(self) -> None:
        self.calls = 0

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        self.calls += 1
        return CURRENT


def _message(
    capabilities: list[str] | None = None, tasks: list[str] | None = None
) -> A2AMessage:
    metadata: dict[str, Any] = {}
    if capabilities is not None:
        metadata[CAPABILITY_IRIS_METADATA_KEY] = capabilities
    if tasks is not None:
        metadata[TASK_IRIS_METADATA_KEY] = tasks
    return A2AMessage(
        role="user",
        message_id="m-1",
        parts=[A2ATextPart(text="summarise the incident")],
        metadata=metadata,
    )


@pytest.fixture
def published_agents(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def send_agent_library(
        client: Any, params: dict[str, Any], graph: Any
    ) -> Any:
        sent.append(params["op"])
        return {"ok": True}

    monkeypatch.setattr(
        "epistemic_graph.generated.storage.send_agent_library", send_agent_library
    )
    return sent


async def test_a_solved_route_is_committed_and_published_with_its_evidence(
    published_agents: list[dict[str, Any]],
) -> None:
    inner, decide = _Inner(), composition(solved(tools=("tool-x", "tool-y")))
    router = DecideA2ARouter(inner, lambda: decide)
    decision = await router.route(
        _message(["cap:incident"], ["eg:task/research"]), context_budget_tokens=None
    )
    assert (decision.agent_name, decision.selected_tools) == (
        "agent-a",
        ("tool-x", "tool-y"),
    )
    assert decision.decision_record_ref == RECORD_ID
    assert decision.agent_graph_ref == "graph-a"
    assert decision.task_iri == "eg:task/research"
    assert inner.calls == 0
    graphs = decide.assembler.graphs
    request = graphs.requests[0]["requirements"]
    assert request["tasks"] == ["eg:task/research"]
    assert request["capabilities"] == ["cap:incident"]
    assert "summarise" not in str(graphs.requests[0])
    assert len(graphs.commits) == 1
    # Agents are published before the graph that pins them.
    assert [op["request"]["entry"]["agent_id"] for op in published_agents] == [
        "agent-a"
    ]
    draft, _context, evidence = graphs.published[0]
    assert draft["graph_id"] == "graph-a"
    assert evidence["component_id"] == RECORD_ID
    kinds = [minted["kind"] for minted in decide.authority.minted]
    assert kinds == ["agent_library_publish", "agent_graph_publish"]


async def test_every_slot_agent_of_a_real_assembly_result_is_published(
    published_agents: list[dict[str, Any]],
) -> None:
    """EH-475: a multi-slot result keeps ALL its agents (``agents`` list)."""
    decide = composition(solved("agent-lead", ("tool-x",), "agent-worker"))
    decision = await DecideA2ARouter(_Inner(), lambda: decide).route(
        _message(["cap:incident"]), context_budget_tokens=None
    )
    assert decision.agent_name == "agent-lead"
    assert [op["request"]["entry"]["agent_id"] for op in published_agents] == [
        "agent-lead",
        "agent-worker",
    ]


async def test_a_failed_publish_keeps_the_committed_route(
    published_agents: list[dict[str, Any]],
) -> None:
    decide = composition(solved())
    decide.assembler.graphs.publish_fails = True
    decision = await DecideA2ARouter(_Inner(), lambda: decide).route(
        _message(["cap:incident"]), context_budget_tokens=None
    )
    assert decision.decision_record_ref == RECORD_ID
    assert decision.agent_graph_ref is None


@pytest.mark.parametrize(
    ("answer", "commit_ok", "installed"),
    [(abstained(), True, True), (solved(), False, True), (solved(), True, False)],
)
async def test_unbudgeted_falls_back_to_current_routing(
    published_agents: list[dict[str, Any]],
    answer: dict[str, Any],
    commit_ok: bool,
    installed: bool,
) -> None:
    inner, decide = _Inner(), composition(answer, commit_ok=commit_ok)
    router = DecideA2ARouter(inner, lambda: decide if installed else None)
    assert (
        await router.route(_message(["cap:x"]), context_budget_tokens=None) == CURRENT
    )
    assert inner.calls == 1
    assert decide.assembler.graphs.published == []


async def test_a_budgeted_route_is_assembled_under_its_budget(
    published_agents: list[dict[str, Any]],
) -> None:
    inner, decide = _Inner(), composition(solved())
    decision = await DecideA2ARouter(inner, lambda: decide).route(
        _message(tasks=["eg:task/review"]), context_budget_tokens=4096
    )
    assert decision.decision_record_ref == RECORD_ID
    request = decide.assembler.graphs.requests[0]["requirements"]
    assert request["constraints"]["context_budget_tokens"] == 4096
    assert inner.calls == 0


@pytest.mark.parametrize(
    ("answer", "installed", "reason"),
    [(abstained(), True, "abstained"), (solved(), False, "no_runner")],
)
async def test_a_budgeted_route_fails_closed_naming_why(
    answer: dict[str, Any], installed: bool, reason: str
) -> None:
    inner, decide = _Inner(), composition(answer)
    router = DecideA2ARouter(inner, lambda: decide if installed else None)
    with pytest.raises(A2AAssemblyUnavailable, match=reason):
        await router.route(
            _message(tasks=["eg:task/review"]), context_budget_tokens=4096
        )
    assert inner.calls == 0
