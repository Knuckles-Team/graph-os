"""Wiring tests for every RF-ADR-009 §2 GraphOS runtime package."""

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


def test_bootstrap_has_no_retired_legacy_query_runtime() -> None:
    bootstrap = (
        Path(__file__).parents[1] / "graph_os" / "mcp_server" / "bootstrap.py"
    ).read_text(encoding="utf-8")

    retired_symbols = {
        "get_tabular_query_service",
        "_get_extraction_manager",
        "get_connection_registry",
        "_resolve_target_engines",
        "_resolve_read_engines",
        "resolve_explicit_graph",
        "bound_to_graph",
        "fanout_execute",
        "_ontology_system",
        "_bind_ontology_package_sync",
        "_bind_mcp_probe_port",
        "_runtime_authority_binders",
        "_sync_ontologies_at_boot",
        "_ingest_self_tool_surface_at_boot",
        "_graphos_self_tool_surface",
        "_mcp_capability_declaration",
        "_ingest_mcp_config_capabilities",
        "_ingest_native_tool_capabilities",
        "_enqueue_fleet_tool_schema_hydration",
        "_hydrate_code_and_configured_connectors",
        "_run_enabled_boot_hydration",
        "derive_capability_synonyms",
        "run_breadth_ingest",
        "sweep_all_sources",
        "_ensure_bundled_skills_ready",
        "_ingest_skill_capabilities",
        "_ingest_prompts_at_boot",
        "_run_boot_hydration_plan",
        "_record_hydration_manifest",
        "get_existing_disabled_batch",
    }
    assert all(symbol not in bootstrap for symbol in retired_symbols)

    runtime = (
        Path(__file__).parents[1] / "graph_os" / "mcp_server" / "runtime.py"
    ).read_text(encoding="utf-8")
    assert "get_existing_disabled_batch" not in runtime
