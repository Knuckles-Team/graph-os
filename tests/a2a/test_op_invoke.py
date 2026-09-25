"""A2A operation projection keeps confirmation bound to the shared invoke path."""

from __future__ import annotations

import sys
from enum import StrEnum
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from graph_os.a2a.op_invoke import OperationProjection, verified_a2a_caller


class Surface(StrEnum):
    A2A = "a2a"


@pytest.fixture(autouse=True)
def _registry_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    api = ModuleType("graph_os.api")
    registry = ModuleType("graph_os.api.registry")
    registry.Surface = Surface  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api", api)
    monkeypatch.setitem(sys.modules, "graph_os.api.registry", registry)


@pytest.mark.asyncio
async def test_plan_confirm_resubmits_exact_op_and_params_to_shared_invoke() -> None:
    calls: list[tuple[Any, ...]] = []

    async def invoke(*args: Any, **kwargs: Any) -> Any:
        calls.append((*args, kwargs))
        return SimpleNamespace(code="OK", details={}, value={"accepted": True})

    projection = OperationProjection(
        services="services", caller=lambda: "verified-caller", invoke_fn=invoke
    )
    answer = await projection.invoke(
        "graphos.plan/confirm",
        {
            "plan_ref": "p1",
            "op": "finance.orders.submit",
            "params": {"order_id": "one"},
            "idempotency_key": "k1",
        },
    )
    assert answer.value == {"accepted": True}
    assert calls == [
        (
            "finance.orders.submit",
            {"order_id": "one"},
            "verified-caller",
            Surface.A2A,
            {"services": "services", "plan_ref": "p1", "idempotency_key": "k1"},
        )
    ]


@pytest.mark.asyncio
async def test_preview_requires_input_and_console_step_up_stays_out_of_band() -> None:
    async def invoke(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            code="STEP_UP_REQUIRED",
            details={"plan_ref": "p2", "console_url": "/console/confirm/p2"},
        )

    projection = OperationProjection(
        services="services", caller=lambda: "verified-caller", invoke_fn=invoke
    )
    answer = await projection.invoke(
        "graphos.op/invoke",
        {"op": "access.approvals.grant", "params": {}},
    )
    assert answer.value == {
        "state": "input-required",
        "code": "STEP_UP_REQUIRED",
        "plan_ref": "p2",
        "preview": {"plan_ref": "p2", "console_url": "/console/confirm/p2"},
    }


@pytest.mark.asyncio
async def test_confirm_rejects_missing_op_and_params() -> None:
    projection = OperationProjection(
        services="services",
        caller=lambda: "verified-caller",
        invoke_fn=lambda *_args, **_kwargs: None,  # never called
    )
    with pytest.raises(ValidationError):
        await projection.invoke("graphos.plan/confirm", {"plan_ref": "p1"})


def test_nonhuman_ambient_actor_never_projects_as_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.api.session import GraphSession, use_session
    from agent_utilities.security.actor_identity import ActorType
    from agent_utilities.security.brain_context import ActorContext

    invoke_module = ModuleType("graph_os.api.invoke")
    invoke_module.VerifiedCaller = SimpleNamespace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api.invoke", invoke_module)
    session = GraphSession(
        actor=ActorContext(
            actor_id="agent:one",
            actor_type=ActorType.AI_AGENT,
            tenant_id="tenant-one",
            authenticated=True,
        ),
        tenant="tenant-one",
        scopes=frozenset({"kg:read"}),
    )
    with use_session(session):
        caller = verified_a2a_caller()
    assert caller.principal_kind == "service"
    assert caller.delegated is True
    assert caller.session is session
