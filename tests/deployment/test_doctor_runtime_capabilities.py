"""The served capability checks retain AU's fail-closed diagnostic contract."""

from __future__ import annotations

from types import SimpleNamespace

from graph_os.deployment import doctor as D
from graph_os.deployment import doctor_runtime_capabilities as C


def test_runtime_capability_checks_are_registered_for_doctor_dispatch(monkeypatch):
    names = ("engine_domains", "graph_authority", "native_optimizer")
    for name in names:
        assert name in D.CHECKS
    assert "native_optimizer" in D._LIVE_CHECK_NAMES

    monkeypatch.setattr(D, "_load_runtime_config", lambda _names: None)
    monkeypatch.setattr(
        C,
        "check_native_optimizer",
        lambda live=False: D._result(
            "native_optimizer", "ok", "live" if live else "static"
        ),
    )
    # Registry entries retain the original callable, so patch that entry explicitly.
    monkeypatch.setitem(D.CHECKS, "native_optimizer", C.check_native_optimizer)
    assert (
        D.run_doctor(only=["native_optimizer"], live=True)["checks"][0]["detail"]
        == "live"
    )


def test_engine_domain_absence_is_failure_outside_tiny(monkeypatch):
    from agent_utilities.core import config
    from agent_utilities.mcp.tools import engine_tools

    monkeypatch.setattr(
        config, "AgentConfig", lambda: SimpleNamespace(deployment_profile="enterprise")
    )
    monkeypatch.setattr(engine_tools, "ENGINE_DOMAINS", {})
    result = C.check_engine_domains()
    assert result["status"] == "fail"
    assert result["data"] == {
        "profile": "enterprise",
        "domain_count": 0,
        "redacted": True,
    }

    monkeypatch.setattr(
        config, "AgentConfig", lambda: SimpleNamespace(deployment_profile="tiny")
    )
    assert C.check_engine_domains()["status"] == "warn"


def test_inactive_graph_authority_is_explicit_skip(monkeypatch):
    from agent_utilities.knowledge_graph import backends

    monkeypatch.setattr(backends, "get_active_backend", lambda: None)
    result = C.check_graph_authority()
    assert result["status"] == "skip"
    assert result["name"] == "graph_authority"


def test_native_optimizer_live_failure_reports_only_bounded_metadata(monkeypatch):
    from agent_utilities.core import config
    from agent_utilities.knowledge_graph.core import graph_compute

    monkeypatch.setattr(
        config, "AgentConfig", lambda: SimpleNamespace(kg_optimization_enabled=True)
    )
    monkeypatch.setattr(
        graph_compute.GraphComputeEngine, "optimize_program", lambda self: None
    )
    monkeypatch.setattr(
        C,
        "_probe_native_optimizer_live",
        lambda: {
            "live_probed": True,
            "operational": False,
            "error_code": "engine_authority_inactive",
            "privacy_safe_payload": True,
        },
    )
    result = C.check_native_optimizer(live=True)
    assert result["status"] == "fail"
    assert result["data"]["error_code"] == "engine_authority_inactive"
    assert "secret" not in str(result).lower()
