"""Fakes at the EG/AU seams of graph-os's Decide consumers."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

CATALOG = "sha256:" + "c" * 64


def record(outcome: str, digest: str = "d" * 64) -> dict[str, Any]:
    reasons = [] if outcome == "solved" else [{"reason": "uncovered_capability"}]
    return {
        "record_id": f"decision:{digest}",
        "record_digest": digest,
        "inputs": {"catalog_digest": CATALOG},
        "outcome": {"outcome": outcome, "reasons": reasons},
    }


def solved(
    agent_id: str = "agent-a", tools: tuple[str, ...] = ("tool-x",)
) -> dict[str, Any]:
    return {
        "record": record("solved"),
        "agents": [
            {"agent_id": agent_id, "tools": [{"component_id": t} for t in tools]}
        ],
    }


def abstained() -> dict[str, Any]:
    return {"record": record("abstained"), "agents": []}


@dataclass
class FakeGraphs:
    answer: dict[str, Any]
    requests: list[dict[str, Any]] = field(default_factory=list)
    commits: list[Any] = field(default_factory=list)

    async def assemble(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(request))
        return self.answer

    async def commit_decision(self, request: Any) -> dict[str, Any]:
        self.commits.append(request)
        return {"record_id": request["record"]["record_id"], "replayed": False}


def assembler(answer: dict[str, Any], *, commit_ok: bool = True) -> Any:
    from agent_utilities.decide.consumers.assembly import Assembler

    from graph_os.decide import DecideCommitRefused

    async def context(record_: Any) -> dict[str, Any]:
        if not commit_ok:
            raise DecideCommitRefused("DecisionCommit is not authorized by policy")
        return {
            "context": {},
            "record": dict(record_),
            "expected_catalog_digest": CATALOG,
        }

    return Assembler(FakeGraphs(answer), "tenant-a", commit_context=context)


def session() -> Any:
    return SimpleNamespace(
        tenant="tenant-a",
        graph="tenant-a",
        actor=SimpleNamespace(actor_id="graph-os-service"),
        policy_version="rev-7",
        trace_context="00-trace",
    )
