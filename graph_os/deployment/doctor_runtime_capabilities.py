"""Read-only health projections for capabilities served by GraphOS.

These checks inspect the composed runtime. They never create an engine or expose
connection material in a doctor result.
"""

from __future__ import annotations

from typing import Any

from .doctor_support import _result


def check_engine_domains() -> dict[str, Any]:
    """A configured deployment must actually ship the required engine tools."""
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.mcp.tools.engine_tools import ENGINE_DOMAINS

        profile = AgentConfig().deployment_profile
        domain_count = len(ENGINE_DOMAINS)
    except Exception as exc:  # noqa: BLE001 - deployment details stay redacted
        return _result(
            "engine_domains",
            "error",
            f"engine domain discovery failed ({type(exc).__name__})",
            data={"redacted": True},
        )
    data = {"profile": profile, "domain_count": domain_count, "redacted": True}
    if domain_count:
        return _result(
            "engine_domains",
            "ok",
            f"{domain_count} engine domain tool families discovered",
            data=data,
        )
    return _result(
        "engine_domains",
        "warn" if profile == "tiny" else "fail",
        "no engine domain tool families registered",
        remediation="Install the epistemic-graph client and restart GraphOS.",
        skill="agent-utilities-deployment",
        data=data,
    )


def check_graph_authority() -> dict[str, Any]:
    """Inspect the current authority; an inactive CLI process is not a failure."""
    try:
        from agent_utilities.knowledge_graph.backends import get_active_backend
        from agent_utilities.knowledge_graph.backends.brain_guarded_backend import (
            BrainGuardedBackend,
        )
        from agent_utilities.knowledge_graph.backends.epistemic_graph_backend import (
            EpistemicGraphBackend,
        )
        from agent_utilities.knowledge_graph.backends.fanout_backend import (
            FanOutBackend,
        )

        backend = get_active_backend()
        if backend is None:
            return _result(
                "graph_authority",
                "skip",
                "no graph authority active in this process",
            )
        inner = backend.inner if isinstance(backend, BrainGuardedBackend) else backend
        authority = inner.authority if isinstance(inner, FanOutBackend) else inner
        if not isinstance(authority, EpistemicGraphBackend):
            return _result(
                "graph_authority",
                "fail",
                "active graph authority is not epistemic-graph",
                remediation="Restart GraphOS with the epistemic-graph authority.",
                skill="database-environment-setup",
                data={"authority_current": False},
            )
        health_check = getattr(authority, "health_check", None)
        healthy = health_check() if callable(health_check) else False
        if not healthy:
            return _result(
                "graph_authority",
                "fail",
                "epistemic-graph authority health check failed",
                remediation="Verify the managed epistemic-graph lifecycle.",
                skill="database-environment-setup",
                data={"authority_current": True},
            )
        return _result(
            "graph_authority",
            "ok",
            "epistemic-graph authority reachable",
            data={
                "authority_current": True,
                "projection_count": len(getattr(inner, "_mirrors", {})),
            },
        )
    except Exception as exc:  # noqa: BLE001 - never expose backend details
        return _result(
            "graph_authority",
            "warn",
            f"authority not evaluable ({type(exc).__name__})",
            remediation="Validate the configured engine lifecycle in GraphOS.",
            skill="database-environment-setup",
            data={"authority_current": False},
        )


def _probe_native_optimizer_live() -> dict[str, Any]:
    """Send only synthetic data through the active ProgramOptimize capability."""
    from agent_utilities.harness.optimization_backend import (
        OptimizationRequest,
        try_native_optimization,
    )
    from agent_utilities.knowledge_graph.core.graph_compute import GraphComputeEngine

    engine = GraphComputeEngine.get_active()
    if engine is None:
        return {
            "live_probed": True,
            "operational": False,
            "error_code": "engine_authority_inactive",
            "privacy_safe_payload": True,
        }
    request = OptimizationRequest(
        target="diagnostic",
        objective="native-capability-probe",
        data={
            "examples": [
                {
                    "task": "synthetic-capability-probe",
                    "response": "synthetic-capability-result",
                    "success": True,
                }
            ]
        },
    )
    attempt = try_native_optimization(engine, request)
    result: dict[str, Any] = {
        "live_probed": True,
        "operational": attempt.disposition == "completed",
        "privacy_safe_payload": True,
    }
    if attempt.disposition != "completed":
        result["error_code"] = attempt.error_code or f"native_{attempt.disposition}"
    return result


def check_native_optimizer(live: bool = False) -> dict[str, Any]:
    """Separate installed optimizer support from an opt-in operational probe."""
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.knowledge_graph.core.graph_compute import (
            GraphComputeEngine,
        )

        enabled = bool(AgentConfig().kg_optimization_enabled)
        available = callable(getattr(GraphComputeEngine, "optimize_program", None))
        data = {
            "enabled": enabled,
            "native_surface_available": available,
            "live_probed": False,
            "operational": None,
            "privacy_safe_payload": True,
        }
        if not enabled:
            return _result(
                "native_optimizer",
                "skip",
                "native program optimization is disabled",
                data=data,
            )
        if not available:
            return _result(
                "native_optimizer",
                "fail",
                "native ProgramOptimize surface is unavailable",
                remediation="Install the unified engine distribution.",
                data=data,
            )
        if not live:
            return _result(
                "native_optimizer",
                "ok",
                "ProgramOptimize surface installed; live proof not requested",
                data=data,
            )
        data.update(_probe_native_optimizer_live())
        if not data["operational"]:
            return _result(
                "native_optimizer",
                "fail",
                "active engine did not complete ProgramOptimize probe",
                remediation="Inspect the active engine health and rerun the live doctor.",
                data=data,
            )
        return _result(
            "native_optimizer",
            "ok",
            "active engine completed ProgramOptimize probe",
            data=data,
        )
    except Exception as exc:  # noqa: BLE001 - never expose optimization material
        return _result(
            "native_optimizer",
            "error",
            f"native optimizer readiness check failed ({type(exc).__name__})",
            data={"redacted": True, "live_probed": live},
        )
