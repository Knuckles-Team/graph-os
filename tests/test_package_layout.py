"""Wiring test: every RF-ADR-009 §2 Phase 5 submodule package is a live import.

Each package below is presently a docstring-only placeholder (no runtime
logic — see `AGENTS.md` "Status" and "W5 source measurements"); importing it
here is what makes it a real, tested member of `graph_os` rather than an
orphan module the orphan-module wiring gate would otherwise have to flag.
"""

from __future__ import annotations

import importlib
from pathlib import Path

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


def test_serving_packages_do_not_import_legacy_au_multiplexer() -> None:
    package_root = Path(__file__).parents[1] / "graph_os"
    offenders = [
        path.relative_to(package_root).as_posix()
        for area in ("fleet", "mcp_server")
        for path in (package_root / area).rglob("*.py")
        if "agent_utilities.mcp.multiplexer" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_deployment_doctor_uses_native_graph_os_hosts() -> None:
    doctor = (
        Path(__file__).parents[1] / "graph_os" / "deployment" / "doctor.py"
    ).read_text(encoding="utf-8")

    assert "agent_utilities.mcp.multiplexer" not in doctor
    assert "agent_utilities.mcp.kg_server" not in doctor
