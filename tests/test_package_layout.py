"""Wiring test: every RF-ADR-009 §2 Phase 5 submodule package is a live import.

Each package below is presently a docstring-only placeholder (no runtime
logic — see `AGENTS.md` "Status" and "W5 source measurements"); importing it
here is what makes it a real, tested member of `graph_os` rather than an
orphan module the orphan-module wiring gate would otherwise have to flag.
"""

from __future__ import annotations

import importlib

import pytest

SUBMODULES = [
    "graph_os.mcp_server",
    "graph_os.fleet",
    "graph_os.gateway",
    "graph_os.control_plane",
    "graph_os.webui_host",
    "graph_os.deployment",
]


@pytest.mark.parametrize("module_name", SUBMODULES)
def test_submodule_imports_and_documents_its_ownership(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__doc__, f"{module_name} must document its RF-ADR-009 ownership"
    assert "RF-ADR-009" in module.__doc__
