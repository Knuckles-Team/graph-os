"""Serve agent-webui from the Graph OS composition boundary.

Graph OS owns agent-webui as the product UI (RF-ADR-009 §2.4).  The public
factory is exported here so composition code does not depend on the module's
implementation path.
"""

from .webui_co_service import run_web_ui

__all__ = ["run_web_ui"]
