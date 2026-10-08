"""Install the agent runtime's process-wide decide consumers at serving start.

GraphOS owns the policy inputs the consumers need. Each install step is
guarded: a missing input logs a reason and skips that consumer. A failure here
never stops serving (GRAPHOS-HOST-R020).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _install_assembler(
    eg_client: Any, session: Any, engine: Any, run: Callable[[Any], Any]
) -> Any:
    from agent_utilities.decide.consumers.assembly import install_library_assembler

    # GraphOS has no commit or publish provider yet: the library still saves
    # each solved graph, uncommitted and unpublished.
    return install_library_assembler(eg_client, session, engine, run=run)


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
    """Install the assembler, task planner and cross-source report.

    Returns which consumers were installed. Each step logs and skips on fault.
    """
    installed = {"assembler": False, "task_planner": False, "cross_source": False}
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
