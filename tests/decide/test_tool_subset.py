"""EH-045: budgeted smallest covering tool subset, evaluate-only with a sampled record."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os import decide as graphos_decide
from graph_os.fleet import decide_tools

from .fakes import abstained, assembler, solved

RANKED = [
    {"kind": "tool", "prefixed_name": "cm__containers", "score": 0.9},
    {"kind": "tool", "prefixed_name": "gl__issues", "score": 0.4},
]


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(answer: dict[str, Any]) -> Any:
        decider = assembler(answer)
        composition = SimpleNamespace(assembler=decider)
        monkeypatch.setattr(graphos_decide, "current_decide", lambda: composition)
        return decider

    return install


async def test_a_solved_subset_is_returned_and_never_committed_unsampled(
    installed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    decider = installed(solved(tools=("tool-x",)))
    monkeypatch.setattr(decide_tools, "_sampled", lambda record: False)
    result = await decide_tools.assembled_tool_subset(RANKED, ["cap:containers"], 4096)
    assert result["decided"] is True and result["tool_ids"] == ["tool-x"]
    assert result["decision_record"] == "decision:" + "d" * 64
    assert result["sampled_commit"] is None
    assert decider.graphs.commits == []
    request = decider.graphs.requests[0]
    assert request["requirements"]["constraints"] == {"context_budget_tokens": 4096}
    assert request["candidates"] == {"kinds": ["tool"]}


async def test_a_sampled_record_is_committed(
    installed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    decider = installed(solved())
    monkeypatch.setattr(decide_tools, "_sampled", lambda record: True)
    result = await decide_tools.assembled_tool_subset(RANKED, ["cap:containers"], 4096)
    assert result["sampled_commit"] == "decision:" + "d" * 64
    assert len(decider.graphs.commits) == 1


async def test_abstention_falls_back_to_the_ranked_tools(installed: Any) -> None:
    installed(abstained())
    result = await decide_tools.assembled_tool_subset(RANKED, ["cap:containers"], 4096)
    assert result["decided"] is False
    assert result["reason"].startswith("abstained")
    assert result["tool_ids"] == ["cm__containers", "gl__issues"]
    assert result["decision_record"] == "decision:" + "d" * 64


@pytest.mark.parametrize(
    ("install", "capabilities", "reason"),
    [(False, ["cap:x"], "no_runner"), (True, [], "no_capabilities")],
)
async def test_without_decide_or_capabilities_the_ranked_tools_answer(
    installed: Any,
    monkeypatch: pytest.MonkeyPatch,
    install: bool,
    capabilities: list[str],
    reason: str,
) -> None:
    if install:
        installed(solved())
    else:
        monkeypatch.setattr(graphos_decide, "current_decide", lambda: None)
    result = await decide_tools.assembled_tool_subset(RANKED, capabilities, 4096)
    assert result == {
        "decided": False,
        "reason": reason,
        "decision_record": None,
        "tool_ids": ["cm__containers", "gl__issues"],
    }
