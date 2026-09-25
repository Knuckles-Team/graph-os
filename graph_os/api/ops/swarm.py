"""Swarm operations require a distinct scoped topology-plan authority.

``decide.assemble`` is the canonical direct AgentAssemble operation. Exposing
the same EG method as ``swarm.plan`` would duplicate the binding and imply
capacity and lease planning that AgentAssemble alone does not provide.
"""

from graph_os.api.registry import OpSpec


def specs() -> tuple[OpSpec, ...]:
    return ()
