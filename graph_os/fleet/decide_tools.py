"""EH-045: the smallest covering tool subset within a context budget (Decide consumer).

``find_tools(context_budget_tokens=...)`` asks EG ``AgentAssemble`` — through
AU's ``tool_subset`` and the boot-installed assembler (:mod:`graph_os.decide`)
— for the smallest tool subset covering the caller's declared capability
IRIs within the budget. The call is EVALUATE-ONLY: nothing is loaded and the
record is not committed per request; a deterministic 1-in-``SAMPLE_EVERY``
sample (by record digest) is committed with ``DecisionCommit`` so the
selection stays auditable.

When EG abstains, is unavailable, no capabilities were declared, or Decide
is not installed, the deterministic fallback answers: the ranked discovery
result's tools in rank order. The requested budget is never silently
ignored — the answer always names whether EG decided and why not.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SAMPLE_EVERY", "assembled_tool_subset"]

SAMPLE_EVERY = 16


def _ranked_tools(ranked: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        str(row["prefixed_name"])
        for row in ranked
        if row.get("kind") == "tool" and row.get("prefixed_name")
    ]


def _fallback(ranked: Sequence[Mapping[str, Any]], reason: str) -> dict[str, Any]:
    return {
        "decided": False,
        "reason": reason,
        "decision_record": None,
        "tool_ids": _ranked_tools(ranked),
    }


def _sampled(record: Mapping[str, Any]) -> bool:
    digest = str(record.get("record_digest") or "")
    return int(hashlib.sha256(digest.encode()).hexdigest(), 16) % SAMPLE_EVERY == 0


async def _sample_commit(assembler: Any, record: Mapping[str, Any]) -> str | None:
    """Commit a sampled evaluate-only record; ``None`` when not sampled."""
    if not _sampled(record) or assembler.commit_context is None:
        return None
    request = await assembler.commit_context(record)
    committed = await assembler.graphs.commit_decision(request)
    payload = getattr(committed, "payload", committed)
    return str(getattr(payload, "record_id", None) or payload["record_id"])


async def assembled_tool_subset(
    ranked: Sequence[Mapping[str, Any]],
    capability_iris: Sequence[str],
    budget: int,
) -> dict[str, Any]:
    """The decided tool subset, or the ranked fallback with its reason."""
    from graph_os.decide import current_decide

    composition = current_decide()
    if composition is None:
        return _fallback(ranked, "no_runner")
    if not capability_iris:
        return _fallback(ranked, "no_capabilities")
    from agent_utilities.decide.consumers.graphos import tool_subset

    fallback_tools = _ranked_tools(ranked)
    tools, answer = await tool_subset(
        composition.assembler,
        list(capability_iris),
        budget,
        lambda reasons: fallback_tools,
    )
    record = (answer.result or {}).get("record") or {}
    result: dict[str, Any] = {
        "decided": answer.agent is not None,
        "reason": answer.reason,
        "decision_record": record.get("record_id"),
        "tool_ids": list(tools),
    }
    if answer.agent is not None:
        try:
            result["sampled_commit"] = await _sample_commit(
                composition.assembler, record
            )
        except (RuntimeError, ConnectionError, TimeoutError, ValueError) as exc:
            result["sampled_commit_error"] = type(exc).__name__
            logger.warning("sampled tool-subset commit failed: %s", exc)
    return result
