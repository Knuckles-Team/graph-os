"""GraphOS serves its own eight-tool MCP intent surface (GRAPHOS-HOST-R023).

Covers the parity and dispatch proofs that requirement names: a tool-list
match against the agent runtime's previously served contract, one dispatch
through the real invocation pipeline (policy/Eunomia included, not a fake),
and the natural-language ``ask`` fallback reached only when no registry
operation matches.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict

from graph_os.api.invoke import InvokeServices, invoke
from graph_os.api.invoke.audit import EffectReservation
from graph_os.api.invoke.plan import EgPlanStore
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.mcp.serving import register_intent_surface
from graph_os.api.mcp.verbs import MCPProjection, dispatch_verb
from graph_os.api.policy.eunomia import PolicyGate
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    Registry,
    Surface,
    Verb,
)
from tests.api._support import make_verified_caller

#: The agent runtime's previously served contract (``register_intent_tools``
#: in ``agent_utilities/mcp/tools/intent_tools.py`` plus the two app launchers
#: from ``agent_utilities/mcp/tools/mcp_apps.py``).
LIVE_CONTRACT_TOOL_NAMES = frozenset(
    {
        "ask",
        "find",
        "why",
        "write",
        "act",
        "manage",
        "graph_task_progress_app",
        "graph_trace_waterfall_app",
    }
)


def test_register_intent_surface_matches_the_live_tool_contract() -> None:
    from fastmcp import FastMCP

    mcp = FastMCP("graph-os-test")
    registry = SimpleNamespace(digest="digest-one", api_version="1")

    async def _unused_invoke(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("not called by this test")

    register_intent_surface(
        mcp,
        registry=registry,
        services=object(),
        resolver=IntentResolver(),
        caller_for_request=lambda: None,
        policy_gate=PolicyGate("none"),
        invoke=_unused_invoke,
    )
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert names == LIVE_CONTRACT_TOOL_NAMES


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 1


_caller = make_verified_caller("example:ask")


def _operation(**changes):
    fields = dict(
        id="example.ask",
        verb=Verb.ASK,
        summary="Fixture ask operation",
        examples=("fixture ask operation",),
        params=Params,
        result=Params,
        binding=Composite(handler="fixture.operation"),
        scopes=frozenset({"example:ask"}),
        effect=Effect.READ,
        audit=AuditClass.EVENT,
        idempotency=Idempotency.NONE,
        surfaces=frozenset(Surface),
    )
    fields.update(changes)
    return OpSpec(**fields)


class _FixtureRuntime:
    service_scopes = frozenset({"example:execute"})

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def verify_current(self, supplied: Any) -> Any:
        return supplied

    @asynccontextmanager
    async def as_caller(self, _supplied: Any):
        yield object()

    @asynccontextmanager
    async def as_service(self, _tenant: str, *, required_scopes: frozenset[str]):
        assert required_scopes <= self.service_scopes
        yield object()

    async def check_subject_access(self, _supplied: Any, _subject: str) -> bool:
        return True

    async def dispatch(self, op: Any, params: Any, context: Any) -> Any:
        self.calls.append((op.id, dict(params)))
        return {"value": params["value"]}


class _FixtureJournal:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    async def reserve(self, key: str, binding: Any) -> EffectReservation:
        if key in self.rows:
            return self.rows[key][1]
        self.rows[key] = (binding, EffectReservation(key, "acquired"))
        return self.rows[key][1]

    async def complete(self, reference: str, outcome: Any) -> None:
        binding, _ = self.rows[reference]
        self.rows[reference] = (
            binding,
            EffectReservation(reference, "completed", outcome),
        )


class _FixtureLeases:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    async def issue(self, **fields: Any) -> dict[str, Any]:
        self.rows[fields["lease_id"]] = {**fields, "status": "active", "revision": 1}
        return {"outcome": "issued"}

    async def get(self, *, tenant: str, lease_id: str) -> dict[str, Any] | None:
        row = self.rows.get(lease_id)
        return dict(row) if row and row["tenant"] == tenant else None

    async def transition(
        self, *, tenant: str, lease_id: str, expected_revision: int, to: str, **_: Any
    ) -> dict[str, Any]:
        row = self.rows[lease_id]
        if row["tenant"] != tenant or row["revision"] != expected_revision:
            return {"outcome": "conflict"}
        row.update(status=to, revision=row["revision"] + 1)
        return {"outcome": "applied"}


async def _audit_preflight(_event: Any, _audit_class: Any, supplied: Any) -> str:
    return "receipt:" + supplied.request_id


async def _audit_write(_event: Any, _audit_class: Any, _supplied: Any) -> None:
    return None


def _build_projection(op: OpSpec) -> tuple[MCPProjection, _FixtureRuntime]:
    runtime = _FixtureRuntime()
    registry = Registry([op])
    services = InvokeServices(
        registry=registry,
        runtime=runtime,
        plans=EgPlanStore(
            SimpleNamespace(control_leases=_FixtureLeases()),
            lease_kind="fixture.plan",
            seal_key=b"x" * 32,
        ),
        policy_gate=PolicyGate("none"),
        audit_preflight=_audit_preflight,
        audit_write=_audit_write,
        effects=_FixtureJournal(),
    )
    projection = MCPProjection(
        registry,
        services,
        IntentResolver(),
        lambda: _caller(),
        PolicyGate("none"),
        invoke,
    )
    return projection, runtime


def test_dispatch_runs_through_the_real_invocation_pipeline() -> None:
    """``ask`` on a real op reaches the real ``invoke`` pipeline, not a stub."""

    projection, runtime = _build_projection(_operation())
    result = asyncio.run(
        dispatch_verb("ask", projection, op="example.ask", params={"value": 7})
    )
    assert result["ok"] is True
    assert result["result"] == {"value": 7}
    assert runtime.calls == [("example.ask", {"value": 7})]


def test_unauthenticated_caller_is_refused_before_the_pipeline() -> None:
    projection, runtime = _build_projection(_operation())
    unauthenticated = replace_caller_for_request(projection, authenticated=False)
    result = asyncio.run(
        dispatch_verb("ask", unauthenticated, op="example.ask", params={"value": 7})
    )
    assert result["ok"] is False
    assert not runtime.calls


def replace_caller_for_request(
    projection: MCPProjection, **changes: Any
) -> MCPProjection:
    from dataclasses import replace

    return replace(projection, caller_for_request=lambda: _caller(**changes))


def test_ask_falls_back_to_nl_query_only_when_no_operation_matches() -> None:
    """The synthetic fallback descriptor never outranks a real matching op."""

    async def _nl_query(intent: str) -> dict[str, Any]:
        return {"answer": intent}

    # No registry ``ask`` operation at all: free text has nothing to match,
    # so the resolver's last resort is the bound natural-language fallback.
    empty_registry = Registry([_operation(id="example.act", verb=Verb.ACT)])
    projection = MCPProjection(
        empty_registry,
        object(),
        IntentResolver(),
        lambda: _caller(),
        PolicyGate("none"),
        _unused_invoke,
        None,
        _nl_query,
    )
    result = asyncio.run(dispatch_verb("ask", projection, intent="what is a widget?"))
    assert result["ok"] is True
    assert result["result"] == {"answer": "what is a widget?"}
    assert result["meta"]["fallback"] is True


def test_ask_fallback_is_not_offered_without_a_bound_nl_query() -> None:
    empty_registry = Registry([_operation(id="example.act", verb=Verb.ACT)])
    projection = MCPProjection(
        empty_registry,
        object(),
        IntentResolver(),
        lambda: _caller(),
        PolicyGate("none"),
        _unused_invoke,
    )
    result = asyncio.run(dispatch_verb("ask", projection, intent="what is a widget?"))
    assert result["ok"] is False


async def _unused_invoke(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("invoke should not be reached by the fallback path")
