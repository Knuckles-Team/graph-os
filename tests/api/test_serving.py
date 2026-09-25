"""The serving root binds one registry to explicit execution authorities."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest
from pydantic import BaseModel

from graph_os.api import serving
from graph_os.api.invoke import VerifiedCaller, invoke
from graph_os.api.policy import PolicyGate
from graph_os.api.registry import (
    EgMethod,
    Executor,
    OpSpec,
    Registry,
    SubjectRef,
    Surface,
    Verb,
)


class Params(BaseModel):
    subject: str


class Result(BaseModel):
    subject: str


class Client:
    @contextmanager
    def use_verified_context(self, claims: dict[str, Any]):
        self.claims = claims
        yield


class Leases:
    async def issue(self, **kwargs: Any) -> None:
        pass

    async def get(self, **kwargs: Any) -> None:
        pass

    async def transition(self, **kwargs: Any) -> None:
        pass


class PlanClient:
    control_leases = Leases()


def op(**changes: Any) -> OpSpec:
    fields = dict(
        id="items.get",
        verb=Verb.ASK,
        summary="Read an item",
        examples=("Show this item",),
        params=Params,
        result=Result,
        binding=EgMethod(service="Items", op="Get"),
        scopes=frozenset({"items:read"}),
    )
    fields.update(changes)
    return OpSpec(**fields)


def ports(calls: list[tuple[str, str]]) -> serving.ServingPorts:
    async def client(tenant: str) -> Client:
        calls.append(("client", tenant))
        return Client()

    async def check_access(
        client: Client, caller: VerifiedCaller, subject: str
    ) -> bool:
        return caller.tenant == "t1" and subject == "item:1"

    async def dispatch(binding: EgMethod, params: dict[str, str], context: Any) -> Any:
        calls.append((context.owner, context.client.claims["principal"]))
        return {"subject": params["subject"]}

    async def audit(event: Any, audit_class: Any) -> None:
        calls.append(("audit", str(audit_class)))

    async def fleet_effect(operation: Any, params: Any, caller: Any) -> Any:
        raise AssertionError("not a fleet call")

    return serving.ServingPorts(
        caller_client=client,
        service_client=client,
        check_access=check_access,
        eg_dispatch=dispatch,
        service_scopes=frozenset({"items:service"}),
        plan_client=PlanClient(),
        policy_gate=PolicyGate("none"),
        audit_write=audit,
        schema_validate=lambda ref, params: params,
        fleet_effect=fleet_effect,
        bindings={},
    )


@pytest.mark.asyncio
async def test_serving_factory_dispatches_under_verified_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([op()]))
    calls: list[tuple[str, str]] = []
    services = serving.build_invoke_services(ports(calls))
    caller = VerifiedCaller(
        principal="user:1",
        tenant="t1",
        effective_scopes=frozenset({"items:read"}),
        engine_claims={
            "principal": "user:1",
            "tenant": "t1",
            "scopes": ["items:read"],
        },
        principal_kind="human",
    )
    result = await invoke(
        "items.get", {"subject": "item:1"}, caller, Surface.MCP, services=services
    )
    assert result.value == {"subject": "item:1"}
    assert calls == [("client", "t1"), ("user:1", "user:1")]


def test_missing_ports_and_service_grants_fail_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([op()]))
    basic = ports([])
    with pytest.raises(ValueError, match="plan_client"):
        serving.build_invoke_services(replace(basic, plan_client=object()))
    with pytest.raises(ValueError, match="policy_gate"):
        serving.build_invoke_services(replace(basic, policy_gate=None))
    with pytest.raises(ValueError, match="eg_dispatch"):
        serving.build_invoke_services(replace(basic, eg_dispatch=None))

    service_op = op(
        executor=Executor.SERVICE,
        executor_scopes=frozenset({"items:service", "items:extra"}),
        subject=SubjectRef(path="subject"),
    )
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([service_op]))
    with pytest.raises(ValueError, match="service grants unavailable"):
        serving.build_invoke_services(basic)


def test_missing_generated_registry_stops_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> Registry:
        raise ValueError("pinned wheel contract missing")

    monkeypatch.setattr(serving, "get_registry", unavailable)
    with pytest.raises(ValueError, match="pinned wheel contract missing"):
        serving.build_invoke_services(ports([]))
