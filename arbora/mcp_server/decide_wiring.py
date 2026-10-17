"""Install the agent runtime's process-wide decide consumers at serving start.

Arbora owns the policy inputs the consumers need. Each install step is
guarded: a missing input logs a reason and skips that consumer. A failure here
never stops serving (ARBORA-HOST-R020, ARBORA-HOST-R021, ARBORA-HOST-R022).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from arbora.api.errors import not_served

logger = logging.getLogger(__name__)


def _mutation_context(session: Any, purpose_id: str) -> dict[str, Any]:
    """A best-effort ``AgentLibraryMutationContext`` for one assembly step.

    EG's admitted-context step replaces the policy fields with its own
    current owner policy digest before any durable commit or publish (see
    ``arbora.api.contracts.provisioning``); this context only has to carry the
    verified session's identity and scope, not a pre-validated policy
    decision.
    """
    actor_id = str(session.actor.actor_id)
    return {
        "request_id": secrets.randbits(63),
        "principal": actor_id,
        "caller_principal": actor_id,
        "attempt_nonce": secrets.token_hex(32),
        "tenant_id": str(session.tenant),
        "actor_scope": actor_id,
        "purpose_id": purpose_id,
        "policy_revision": str(session.policy_version),
        "policy_digest": f"sha256:{session.policy_version}",
        "policy_decision_id": "",
        "idempotency_key": secrets.token_hex(16),
        "expected_revision": None,
        "trace_id": str(session.trace_context),
        "created_at_ms": int(time.time() * 1000),
    }


def _commit_context(session: Any) -> Callable[[Mapping[str, Any]], Any]:
    """The assembler's ``commit_context`` provider, bound to ``session``."""

    async def provider(_record: Mapping[str, Any]) -> Mapping[str, Any]:
        return _mutation_context(session, "agent_library.assembly_commit")

    return provider


def _publish_context(session: Any) -> Callable[[Mapping[str, Any]], Any]:
    """The assembler's ``publish_context`` provider, bound to ``session``."""

    async def provider(_graph: Mapping[str, Any]) -> Mapping[str, Any]:
        return _mutation_context(session, "agent_library.assembly_publish")

    return provider


def _install_assembler(
    eg_client: Any, session: Any, engine: Any, run: Callable[[Any], Any]
) -> Any:
    raise not_served("decide.consumers.assembly", "EG-DECISION-ENGINE-R036")


def _published_templates() -> list[dict[str, Any]]:
    raise not_served("decide.topology.templates", "EG-DECISION-ENGINE-R105")


def _install_topology(assembler: Any, run: Callable[[Any], Any]) -> None:
    raise not_served("decide.consumers.topology", "EG-DECISION-ENGINE-R036")


async def _search_agent_cards(eg_client: Any, session: Any) -> Any:
    """Search the session's graph for registered A2A agent cards through EG."""
    import importlib

    component = importlib.import_module("epistemic_graph.generated.agent_component")
    storage = importlib.import_module("epistemic_graph.generated.storage")
    graph = str(getattr(session, "graph", "") or "").strip()
    if not graph:
        raise LookupError("the verified session is not bound to a graph")
    request = component.AgentComponentSearchRequest(
        tenant_id=session.tenant,
        kinds=[component.AgentComponentKind.A2A_AGENT_CARD],
        limit=64,
        cursor=None,
    )
    return await storage.send_agent_component_search(eg_client, request, graph)


def _capability_search(eg_client: Any, session: Any) -> Callable[[Sequence[str]], Any]:
    """The planner's reuse lookup: registered A2A agent cards, served from EG."""

    async def lookup(_task_iris: Sequence[str]) -> list[dict[str, Any]]:
        try:
            result = await _search_agent_cards(eg_client, session)
        except Exception as exc:
            logger.warning("capability search unavailable: %s", exc)
            return []
        rows = getattr(result, "components", None)
        if rows is None:
            rows = result if isinstance(result, Sequence) else []
        hits: list[dict[str, Any]] = []
        for row in rows:
            component_id = (
                row.get("component_id")
                if isinstance(row, Mapping)
                else getattr(row, "component_id", None)
            )
            if component_id:
                hits.append({"kind": "a2a_agent", "id": str(component_id)})
        return hits

    return lookup


def _guardrail_source(engine: Any) -> Callable[[Sequence[str]], Any]:
    """The planner's policy rules: one side-effect-free AU action-policy
    verdict per task (never raises; fails closed -- AU-CONTROL-R017)."""
    from arbora.access.action_policy import (
        ActionRequest,
        get_action_policy,
    )

    policy = get_action_policy(engine)

    async def lookup(tasks: Sequence[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in tasks:
            decision = policy.evaluate(
                ActionRequest(kind="agent_execute", target=task, source="task_planner")
            )
            rows.append(
                {
                    "id": f"action-policy:{task}",
                    "requirement": "AU-CONTROL-R017",
                    "rule": f"{decision.decision} at tier {decision.tier} "
                    f"({decision.reason})",
                }
            )
        return rows

    return lookup


def _workflow_lookup(engine: Any) -> Callable[[Sequence[str]], Any]:
    """The planner's compiled-workflow lookup: the registered graph workflow
    store's semantic match over the planned tasks."""
    raise not_served("knowledge_graph.workflow_store", "EG-CQ-ORCH-R700")


def _install_planner(
    assembler: Any,
    run: Callable[[Any], Any],
    eg_client: Any,
    session: Any,
    engine: Any,
) -> None:
    raise not_served("decide.consumers.task_planner", "EG-CQ-ORCH-R001")


def _ontology_provider(eg_client: Any) -> Any:
    sparql = getattr(eg_client, "sparql", None)
    if not callable(sparql):
        return None
    raise not_served(
        "knowledge_graph.virtual_graph.ontology", "EG-UNIFIED-DATA-PLANE-R037"
    )


def _install_cross_source(eg_client: Any) -> bool:
    raise not_served(
        "knowledge_graph.virtual_graph.federation", "EG-UNIFIED-DATA-PLANE-R037"
    )


def install_decide_consumers(
    client_for_session: Callable[[Any], Any],
    session: Any,
    engine: Any,
    *,
    run: Callable[[Any], Any] = asyncio.run,
) -> dict[str, bool]:
    """Install the assembler, topology asker, task planner and cross-source
    report.

    Returns which consumers were installed. Each step logs and skips on fault.
    """
    installed = {
        "assembler": False,
        "topology": False,
        "task_planner": False,
        "cross_source": False,
    }
    assembler = None
    try:
        eg_client = client_for_session(session) if session is not None else None
    except Exception as exc:
        logger.warning("decide consumers skipped: no engine client: %s", exc)
        return installed
    if eg_client is None:
        logger.info("decide consumers skipped: no engine client or session")
        return installed
    try:
        assembler = _install_assembler(eg_client, session, engine, run)
        installed["assembler"] = True
    except Exception as exc:
        logger.warning("assembler install skipped: %s", exc)
    if assembler is not None:
        try:
            _install_topology(assembler, run)
            installed["topology"] = True
        except Exception as exc:
            logger.warning("topology asker install skipped: %s", exc)
        try:
            _install_planner(assembler, run, eg_client, session, engine)
            installed["task_planner"] = True
        except Exception as exc:
            logger.warning("task planner install skipped: %s", exc)
    try:
        installed["cross_source"] = _install_cross_source(eg_client)
    except Exception as exc:
        logger.warning("cross-source install skipped: %s", exc)
    return installed


__all__ = ["install_decide_consumers"]
