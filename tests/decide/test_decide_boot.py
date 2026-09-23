"""graph-os boot installs AU's Decide runner and assembler (decide-consumers contract)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os import decide as graphos_decide

from .fakes import CATALOG, record, session


class _Components:
    def __init__(self, published: dict[str, Any]) -> None:
        self.published = published

    async def current(self, request: Any) -> Any:
        return self.published.get(request.component_id)


def _entry(component_id: str, kind: str) -> Any:
    return SimpleNamespace(
        component_id=component_id,
        kind=SimpleNamespace(value=kind),
        definition_digest="sha256:" + "a" * 64,
    )


async def test_only_published_points_are_bound() -> None:
    components = _Components(
        {
            "decide.schema.au.route.cost": _entry(
                "decide.schema.au.route.cost", "feature_schema"
            ),
            "decide.head.au.route.cost": _entry(
                "decide.head.au.route.cost", "decision_head"
            ),
            "decide.schema.au.retrieval.plan": _entry(
                "decide.schema.au.retrieval.plan", "feature_schema"
            ),
        }
    )
    bindings = await graphos_decide.resolve_bindings(components, "tenant-a")
    assert set(bindings.by_question) == {"au.route.cost", "au.retrieval.plan"}
    cost = bindings.by_question["au.route.cost"]
    assert cost.feature_schema["kind"] == "feature_schema"
    assert cost.head is not None and cost.head["kind"] == "decision_head"
    assert bindings.by_question["au.retrieval.plan"].head is None


class _Policy:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.requests: list[Any] = []

    def decide(self, request: Any) -> Any:
        self.requests.append(request)
        receipt = SimpleNamespace(
            authorizes_effect=self.allowed,
            request_digest=request.digest(),
            receipt_id="receipt-1",
        )
        return SimpleNamespace(allowed=self.allowed, receipt=receipt)


async def test_commit_context_is_minted_from_a_policy_receipt() -> None:
    policy = _Policy(allowed=True)
    provide = graphos_decide.decision_commit_context(session(), policy)
    request = await provide(record("solved"))
    context = request["context"]
    assert request["expected_catalog_digest"] == CATALOG
    assert context["tenant_id"] == "tenant-a"
    assert context["policy_decision_id"] == "receipt-1"
    assert context["purpose_id"] == "decision:commit"
    assert context["principal"].startswith("principal:sha256:")
    assert policy.requests[0].kind == "decision_commit"


async def test_a_denied_receipt_refuses_the_commit() -> None:
    provide = graphos_decide.decision_commit_context(session(), _Policy(allowed=False))
    with pytest.raises(graphos_decide.DecideCommitRefused):
        await provide(record("solved"))


def test_boot_installs_and_uninstalls_the_runner_and_assembler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities import decide
    from agent_utilities.decide import StaticBindings
    from agent_utilities.decide.consumers import assembly

    async def no_bindings(components: Any, tenant: str) -> Any:
        return StaticBindings({})

    monkeypatch.setattr(graphos_decide, "resolve_bindings", no_bindings)
    composition = graphos_decide.install_decide(
        SimpleNamespace(), session(), _Policy(allowed=True)
    )
    try:
        runner = decide.current_runner()
        assert runner is not None and runner is composition.runner
        assert runner.tenant == "tenant-a"
        assert assembly._INSTALLED[0] is not None
        assert graphos_decide.current_decide() is composition
    finally:
        composition.uninstall()
    assert decide.current_runner() is None
    assert graphos_decide.current_decide() is None


def test_a_failed_boot_keeps_every_point_on_its_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_args: Any) -> Any:
        raise RuntimeError("engine does not serve AgentComponent")

    monkeypatch.setattr(graphos_decide, "install_decide", broken)
    assert (
        graphos_decide.install_decide_at_boot(lambda tenant: None, session(), None)
        is None
    )
    assert graphos_decide.current_decide() is None
