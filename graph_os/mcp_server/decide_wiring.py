"""Install the agent runtime's process-wide decide consumers at serving start.

GraphOS owns the policy inputs the consumers need. Each install step is
guarded: a missing input logs a reason and skips that consumer. A failure here
never stops serving (GRAPHOS-HOST-R020, GRAPHOS-HOST-R021).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)


def _mutation_context(session: Any, purpose_id: str) -> dict[str, Any]:
    """A best-effort ``AgentLibraryMutationContext`` for one assembly step.

    EG's admitted-context step replaces the policy fields with its own
    current owner policy digest before any durable commit or publish (see
    ``agent_utilities.api.provisioning``); this context only has to carry the
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
    from agent_utilities.decide.consumers.assembly import install_library_assembler

    return install_library_assembler(
        eg_client,
        session,
        engine,
        run=run,
        commit_context=_commit_context(session),
        publish_context=_publish_context(session),
    )


def _published_templates() -> list[dict[str, Any]]:
    from agent_utilities.decide.topology.templates import (
        REFERENCE_TEMPLATES,
        topology_facts,
    )

    return [topology_facts(spec) for spec in REFERENCE_TEMPLATES]


def _install_topology(assembler: Any, run: Callable[[Any], Any]) -> None:
    from agent_utilities.decide.consumers import topology

    topology.install_topology(assembler, run, _published_templates)


def _install_planner(assembler: Any, run: Callable[[Any], Any]) -> None:
    from agent_utilities.decide.consumers import task_planner as tp

    templates = getattr(tp, "installed_templates", lambda: None)()
    tp.install_task_planner(
        tp.TaskPlanner(assembler=assembler, templates=templates, driver=run)
    )


def _ontology_provider(eg_client: Any) -> Any:
    sparql = getattr(eg_client, "sparql", None)
    if not callable(sparql):
        return None
    from agent_utilities.knowledge_graph.virtual_graph.ontology import (
        tbox_from_sparql,
    )

    async def provider() -> Any:
        return await tbox_from_sparql(sparql)

    return provider


def _install_cross_source(eg_client: Any) -> bool:
    from agent_utilities.knowledge_graph.virtual_graph.federation import (
        install_cross_source,
    )

    from graph_os.fleet.access_contracts import fleet_virtual_catalog

    provider = _ontology_provider(eg_client)
    if provider is None:
        logger.info("cross-source report skipped: the engine client has no sparql")
        return False
    install_cross_source(fleet_virtual_catalog(), provider)
    return True


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
            _install_planner(assembler, run)
            installed["task_planner"] = True
        except Exception as exc:
            logger.warning("task planner install skipped: %s", exc)
    try:
        installed["cross_source"] = _install_cross_source(eg_client)
    except Exception as exc:
        logger.warning("cross-source install skipped: %s", exc)
    return installed


__all__ = ["install_decide_consumers"]
