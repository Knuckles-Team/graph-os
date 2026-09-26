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
        services="services",
        caller=lambda: SimpleNamespace(principal_kind="human", delegated=False),
        invoke_fn=invoke,
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
            SimpleNamespace(principal_kind="human", delegated=False),
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
        services="services",
        caller=lambda: SimpleNamespace(principal_kind="human", delegated=False),
        invoke_fn=invoke,
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
        "status": {
            "state": "input-required",
            "message": {
                "role": "agent",
                "parts": [{"kind": "text", "text": "Open the console to confirm"}],
                "messageId": "p2",
                "metadata": {"graphOsConsoleUrl": "/console/confirm/p2"},
            },
        },
    }


@pytest.mark.asyncio
async def test_plan_preview_carries_exact_authenticated_confirmation_input() -> None:
    async def invoke(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            code="CONFIRMATION_REQUIRED",
            details={"plan_ref": "p3", "op": "finance.orders.submit"},
        )

    projection = OperationProjection(
        services="services",
        caller=lambda: SimpleNamespace(principal_kind="human", delegated=False),
        invoke_fn=invoke,
    )
    params = {"order_id": "one", "limits": {"quantity": 3}}
    answer = await projection.invoke(
        "graphos.op/invoke", {"op": "finance.orders.submit", "params": params}
    )
    assert answer.value["status"]["message"]["metadata"] == {
        "graphOsPlan": {
            "plan_ref": "p3",
            "op": "finance.orders.submit",
            "params": params,
            "confirm": "plan",
        }
    }


@pytest.mark.asyncio
async def test_missing_plan_binding_fails_closed() -> None:
    async def invoke(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(code="CONFIRMATION_REQUIRED", details={})

    projection = OperationProjection(
        services="services", caller=lambda: "verified-caller", invoke_fn=invoke
    )
    answer = await projection.invoke(
        "graphos.op/invoke", {"op": "finance.orders.submit", "params": {}}
    )
    assert answer.refused is True
    assert answer.code == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_confirm_rejects_missing_op_and_params() -> None:
    projection = OperationProjection(
        services="services",
        caller=lambda: SimpleNamespace(principal_kind="human", delegated=False),
        invoke_fn=lambda *_args, **_kwargs: None,  # never called
    )
    with pytest.raises(ValidationError):
        await projection.invoke("graphos.plan/confirm", {"plan_ref": "p1"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("principal_kind", "delegated"),
    [("service", False), ("human", True)],
)
async def test_plan_confirm_rejects_nonhuman_or_delegated_caller(
    principal_kind: str, delegated: bool
) -> None:
    calls: list[Any] = []

    async def invoke(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return SimpleNamespace(code="OK", details={}, value={})

    projection = OperationProjection(
        services="services",
        caller=lambda: SimpleNamespace(
            principal_kind=principal_kind, delegated=delegated
        ),
        invoke_fn=invoke,
    )
    answer = await projection.invoke(
        "graphos.plan/confirm", {"plan_ref": "p1", "op": "op", "params": {}}
    )
    assert answer.refused is True
    assert answer.code == "FORBIDDEN"
    assert calls == []


def test_nonhuman_ambient_actor_never_projects_as_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.api.session import GraphSession, use_session
    from agent_utilities.security.actor_identity import ActorType
    from agent_utilities.security.brain_context import ActorContext

    invoke_module = ModuleType("graph_os.api.invoke")
    invoke_module.VerifiedCaller = SimpleNamespace(  # type: ignore[attr-defined]
        from_session=lambda session, **_kwargs: SimpleNamespace(
            principal_kind="service", delegated=True, session=session
        )
    )
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
