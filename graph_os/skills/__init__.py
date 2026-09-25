"""Operator skills bundled with Graph OS.

``graphos-genesis`` provisions the Day-0 substrate (Docker Compose on one host,
or Kubernetes through the chart in ``deploy/helm/graph-os``) and hands off to
``graphos-deployment``, which installs, configures and verifies the Graph OS
runtime. Discovered through the ``agent_utilities.skill_providers`` entry point.
"""

from __future__ import annotations

GRAPHOS_SKILLS: tuple[str, ...] = ("graphos-deployment", "graphos-genesis")

__all__ = ["GRAPHOS_SKILLS"]
