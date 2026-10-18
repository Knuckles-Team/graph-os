"""Arbora's own ecosystem-governance modules (ARBORA-HOST-R008).

Hosts the agent-manager-dashboard module moved from the agent runtime.
Further AU-BOUNDARY-R014 modules land here incrementally, one
ARBORA-HOST-R008 slice at a time.
"""

from __future__ import annotations

from .agent_manager_dashboard import AgentManagerDashboard, DashboardReport

__all__ = ["AgentManagerDashboard", "DashboardReport"]
