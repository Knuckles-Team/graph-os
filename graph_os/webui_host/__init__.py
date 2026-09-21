"""Serve agent-webui from the Graph OS composition boundary.

Graph OS owns agent-webui as the product UI (RF-ADR-009 §2.4). The public host
and application composer are exported here so composition code does not depend
on the module's implementation path.
"""

from .webui_co_service import compose_web_application, run_web_ui

__all__ = ["compose_web_application", "run_web_ui"]
