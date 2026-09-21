"""Graph-os deployment tooling (RF-ADR-009 phase 5).

This package owns host-facing deployment composition: profile/config generation,
preflight and doctor checks, release canary, production backup/restore validation,
the safe venv reconciler, and deterministic backend plans.  The AU release and
skill/connector certification implementations remain AU-owned product tooling;
the doctor consumes those checks without extracting them.

The public console scripts are registered in ``pyproject.toml`` and point here
directly.  There are no compatibility aliases back to ``agent_utilities``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .codex_registration import (
    CODEX_GRAPHOS_COMMAND,
    CODEX_GRAPHOS_SERVER,
    CodexRegistrationError,
    graphos_stdio_spec,
    register_codex_graphos,
)
from .config_generator import (
    PROFILES,
    config_doctor,
    config_reference,
    generate_config,
    is_restart_required,
    write_config,
)
from .genesis_environments import (
    EnvironmentProfile,
    EnvironmentProfileError,
    MissingSecretReferenceError,
    list_environment_profiles,
    load_environment_profile,
    profile_summary,
    validate_environment_profile,
)
from .repo_templates import (
    CI_TEMPLATES,
    PROFILE_REPO_SETS,
    STANDARD_REPOS,
    RepoTemplate,
    manifest_summary,
    provision_plan,
    render_skeleton,
    runner_plan,
    standard_repos,
)
from .self_deploy import execute_redeploy, plan_redeploy

if TYPE_CHECKING:
    from .doctor import CHECKS, run_doctor
    from .preflight import run_preflight

_LAZY_SUBMODULES = {
    "doctor": ".doctor",
    "preflight": ".preflight",
}

__all__ = [
    "CHECKS",
    "CI_TEMPLATES",
    "CODEX_GRAPHOS_COMMAND",
    "CODEX_GRAPHOS_SERVER",
    "CodexRegistrationError",
    "EnvironmentProfile",
    "EnvironmentProfileError",
    "MissingSecretReferenceError",
    "PROFILES",
    "PROFILE_REPO_SETS",
    "STANDARD_REPOS",
    "RepoTemplate",
    "config_doctor",
    "config_reference",
    "generate_config",
    "graphos_stdio_spec",
    "is_restart_required",
    "list_environment_profiles",
    "load_environment_profile",
    "manifest_summary",
    "profile_summary",
    "provision_plan",
    "render_skeleton",
    "register_codex_graphos",
    "execute_redeploy",
    "plan_redeploy",
    "run_doctor",
    "run_preflight",
    "runner_plan",
    "standard_repos",
    "validate_environment_profile",
    "write_config",
]


def _load_submodule(name: str) -> Any:
    """Import and cache one fixed deployment submodule on first access."""
    module = import_module(_LAZY_SUBMODULES[name], __name__)
    globals()[name] = module
    return module


def __getattr__(name: str) -> Any:
    """Load doctor/preflight exports only when a caller actually requests them.

    ``python -m graph_os.deployment.doctor`` imports this package before
    executing its target module. Eagerly importing ``doctor`` here therefore
    pre-populated ``sys.modules`` and made :mod:`runpy` emit a RuntimeWarning.
    Keep the public facade intact without pre-importing the module entry point.
    """
    if name in _LAZY_SUBMODULES:
        return _load_submodule(name)
    if name in {"CHECKS", "run_doctor"}:
        doctor = _load_submodule("doctor")

        exports = {"CHECKS": doctor.CHECKS, "run_doctor": doctor.run_doctor}
        globals().update(exports)
        return exports[name]
    if name == "run_preflight":
        preflight = _load_submodule("preflight")

        globals()[name] = preflight.run_preflight
        return preflight.run_preflight
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose static and lazy public members to discovery/introspection callers."""
    return sorted(set(globals()) | set(__all__) | set(_LAZY_SUBMODULES))
