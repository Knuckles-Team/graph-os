"""Certification commands resolve to the GraphOS deployment package."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

CERTIFICATION_SCRIPTS = (
    "agent-utilities-validate-skills",
    "agent-utilities-validate-skill-fleet",
    "graph-os-certify-skills",
    "graph-os-generate-skill-runtime-profile",
    "graph-os-generate-skill-certification",
    "graph-os-skill-readiness",
    "graph-os-verify-skill-certification",
)


def test_certification_console_scripts_resolve_locally() -> None:
    root = Path(__file__).resolve().parents[2]
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    for name in CERTIFICATION_SCRIPTS:
        module_name, function_name = scripts[name].split(":", 1)
        assert module_name.startswith("graph_os.deployment.")
        assert callable(getattr(importlib.import_module(module_name), function_name))
