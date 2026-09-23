"""EH-044: A2A inbound routing consults EG assembly first, falls back otherwise."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.a2a.decide_routing import CAPABILITY_IRIS_METADATA_KEY, DecideA2ARouter
from graph_os.a2a.models import A2AMessage, A2ARouteDecision, A2ATextPart

from .fakes import abstained, assembler, solved

CURRENT = A2ARouteDecision(agent_name="current-agent", selection_mode="current")


class _Inner:
    def __init__(self) -> None:
        self.calls = 0

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        self.calls += 1
        return CURRENT


def _message(capabilities: list[str] | None = None) -> A2AMessage:
    metadata = (
        {} if capabilities is None else {CAPABILITY_IRIS_METADATA_KEY: capabilities}
    )
    return A2AMessage(
        role="user",
        message_id="m-1",
        parts=[A2ATextPart(text="summarise the incident")],
        metadata=metadata,
    )


async def test_a_solved_and_committed_assembly_routes_the_task() -> None:
    inner = _Inner()
    decider = assembler(solved(tools=("tool-x", "tool-y")))
    router = DecideA2ARouter(inner, lambda: decider)
    decision = await router.route(
        _message(["cap:summarise", "cap:incident"]), context_budget_tokens=None
    )
    assert decision.agent_name == "agent-a"
    assert decision.selected_tools == ("tool-x", "tool-y")
    assert decision.selection_mode == "eg-decide-assembly"
    assert decision.decision_record_ref == "decision:" + "d" * 64
    assert inner.calls == 0
    request = decider.graphs.requests[0]
    assert request["requirements"]["capabilities"] == ["cap:incident", "cap:summarise"]
    assert len(decider.graphs.commits) == 1


@pytest.mark.parametrize(
    ("capabilities", "answer", "commit_ok"),
    [
        (["cap:summarise"], abstained(), True),
        (["cap:summarise"], solved(), False),
        (None, solved(), True),
    ],
)
async def test_everything_else_falls_back_to_current_routing(
    capabilities: list[str] | None, answer: dict[str, Any], commit_ok: bool
) -> None:
    inner = _Inner()
    decider = assembler(answer, commit_ok=commit_ok)
    router = DecideA2ARouter(inner, lambda: decider)
    assert (
        await router.route(_message(capabilities), context_budget_tokens=None)
        == CURRENT
    )
    assert inner.calls == 1
    assert decider.graphs.commits == []


async def test_no_installed_assembler_and_budgeted_requests_keep_current_path() -> None:
    inner = _Inner()
    router = DecideA2ARouter(inner, lambda: None)
    assert (
        await router.route(_message(["cap:x"]), context_budget_tokens=None) == CURRENT
    )
    decider = assembler(solved())
    budgeted = DecideA2ARouter(inner, lambda: decider)
    assert (
        await budgeted.route(_message(["cap:x"]), context_budget_tokens=4096) == CURRENT
    )
    assert decider.graphs.requests == []
