"""GraphOS deployment planner contract at its owning package."""

from __future__ import annotations

import pytest

from graph_os.deployment import backends
from graph_os.mcp_server.composition import CompositionPlan


@pytest.fixture(autouse=True)
def _composition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backends,
        "detect_composition",
        lambda engine=None: CompositionPlan(web_ui_enabled=True),
    )


@pytest.mark.parametrize(
    ("name", "live_capable"),
    [
        ("in_process", True),
        ("container", False),
        ("kubernetes", False),
        ("native_shell", False),
    ],
)
def test_each_backend_plans_real_graph_os_entrypoint(
    name: backends.BackendName, live_capable: bool
) -> None:
    backend = backends.get_backend(name)
    plan = backend.plan(target="example")
    assert plan.live_capable is live_capable
    assert plan.steps
    assert "agent-webui" in plan.rendered_summary()
    if name == "native_shell":
        assert (
            "graph-os --transport streamable-http" in plan.artifacts["graph-os.service"]
        )


def test_live_apply_requires_verified_session() -> None:
    backend = backends.InProcessBackend()
    with pytest.raises(ValueError, match="verified session"):
        backend.apply(backend.plan(), dry_run=False)


@pytest.mark.parametrize("name", ["container", "kubernetes", "native_shell"])
def test_plan_only_backends_refuse_live_apply(name: backends.BackendName) -> None:
    backend = backends.get_backend(name)
    with pytest.raises(backends.PlanOnlyBackendError):
        backend.apply(backend.plan(target="example"), dry_run=False)
