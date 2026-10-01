"""Focused MCP verb projection checks; server cutover is covered separately.

``invoke`` (GRAPHOS-OPS-R007's single invocation chokepoint) is injected into
``MCPProjection`` rather than imported from ``graph_os.api.invoke``, which
does not exist on this branch yet; each test supplies its own fake.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from graph_os.api.mcp.discovery import find_visible
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.mcp.resources import operation_spec, registry_index
from graph_os.api.mcp.verbs import PARAMETERS, MCPProjection, dispatch_verb, make_verb
from graph_os.api.policy import PolicyGate, PolicyUnavailable
from graph_os.api.registry import Effect, Surface, Verb


class Input(BaseModel):
    value: str


class Output(BaseModel):
    ok: bool


@dataclass
class Caller:
    principal: str = "person"
    tenant: str = "tenant-one"
    principal_kind: str = "human"
    delegated: bool = False
    authenticated: bool = True
    effective_scopes: frozenset[str] = frozenset({"mcp:discover", "read", "write"})
    policy_revision: str = "rev-one"
    request_id: str = "request-one"


def _op(op_id: str, verb: Verb, scope: str) -> Any:
    from graph_os.api.registry import Confirm, Effect, PrincipalRule, Stability

    return SimpleNamespace(
        id=op_id,
        verb=verb,
        summary="Operation summary",
        params=Input,
        result=Output,
        scopes=frozenset({scope}),
        effect=Effect.READ if verb == Verb.ASK else Effect.WRITE,
        confirm=Confirm.NONE,
        stability=Stability.STABLE,
        examples=("Example request",),
        principals=PrincipalRule.ANY,
        surfaces=frozenset({Surface.MCP}),
    )


class Registry:
    digest = "digest-one"
    api_version = "1"

    def __init__(self, *ops: Any) -> None:
        self.ops = {op.id: op for op in ops}

    def __iter__(self):
        return iter(self.ops.values())

    def get(self, op_id: str) -> Any:
        return self.ops.get(op_id)

    def find(
        self, caller: Caller, *, policy: Any, surface: Surface, verb: Verb | None = None
    ):
        return tuple(
            op
            for op in self.ops.values()
            if surface in op.surfaces
            and (verb is None or op.verb == verb)
            and op.scopes.issubset(caller.effective_scopes)
            and policy(op, caller)
        )


class Resolver:
    def scope_ref(self, *_args: Any, **_kwargs: Any) -> str:
        return "scope-ref"

    def rank(
        self, _verb: str, _intent: str, items: Any, **_kwargs: Any
    ) -> tuple[Any, ...]:
        return tuple(
            SimpleNamespace(descriptor=SimpleNamespace(id=item.get("id") or item["op"]))
            for item in items
        )

    def resolve(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            op="things.update",
            params={"value": "new"},
            missing_required=(),
            alternatives=(),
            why="Matched update",
        )


async def _unused_invoke(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("invoke should not be reached by this test")


def _op_result(value: Any = None, *, code: str = "OK", details: Any = None) -> Any:
    """A stand-in for ``OpResult`` (GRAPHOS-OPS-R007), checked structurally."""

    return SimpleNamespace(value=value, code=code, details=details or {})


def _projection(
    registry: Registry,
    *,
    policy_gate: Any = None,
    fleet: Any = None,
    invoke: Any = _unused_invoke,
) -> MCPProjection:
    return MCPProjection(
        registry,
        object(),
        Resolver(),
        Caller,
        policy_gate or PolicyGate("none"),
        invoke,
        fleet,
    )


def test_find_merges_only_authorized_registry_and_filtered_fleet_entries() -> None:
    registry = Registry(
        _op("things.read", Verb.ASK, "read"), _op("things.admin", Verb.MANAGE, "admin")
    )

    async def fleet(**_kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": "child__tool", "summary": "A visible child tool"}]

    found = asyncio.run(
        find_visible(
            registry=registry,
            caller=Caller(),
            policy_gate=PolicyGate("none"),
            resolver=Resolver(),
            fleet_search=fleet,
            intent="things",
        )
    )
    assert [item.get("id") or item["op"] for item in found["items"]] == [
        "things.read",
        "child__tool",
    ]
    assert found["items"][0]["params_schema"]["properties"]["value"]["type"] == "string"
    assert found["items"][1]["op"] == "fleet.call"


def test_real_resolver_keeps_distinct_fleet_items_when_merging_find() -> None:
    async def fleet(**_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"id": "alpha__search", "summary": "Search alpha records"},
            {"id": "beta__search", "summary": "Search beta records"},
        ]

    result = asyncio.run(
        find_visible(
            registry=Registry(),
            caller=Caller(),
            policy_gate=PolicyGate("none"),
            resolver=IntentResolver(),
            fleet_search=fleet,
            intent="search",
            scope_ref="verified-scope",
        )
    )
    assert {item["id"] for item in result["items"]} == {
        "alpha__search",
        "beta__search",
    }


def test_direct_verb_mismatch_refuses_before_invocation() -> None:
    registry = Registry(_op("things.update", Verb.WRITE, "write"))
    result = asyncio.run(
        dispatch_verb(
            "ask", _projection(registry), op="things.update", params={"value": "x"}
        )
    )
    assert result["error"]["code"] == "VERB_MISMATCH"


def test_natural_language_write_uses_preview_path() -> None:
    received: dict[str, Any] = {}

    async def invoke(*_args: Any, **kwargs: Any) -> Any:
        received.update(kwargs)
        return _op_result(
            code="CONFIRMATION_REQUIRED", details={"plan_ref": "bound-plan"}
        )

    registry = Registry(_op("things.update", Verb.WRITE, "write"))
    result = asyncio.run(
        dispatch_verb(
            "write",
            _projection(registry, invoke=invoke),
            intent="update thing",
            params={"value": "new"},
        )
    )
    assert received["resolved_from_intent"] is True
    assert result["meta"]["plan"]["plan_ref"] == "bound-plan"


def test_exact_find_uses_governed_invoke_and_preview_stays_discovery() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    async def invoke(op: str, params: Any, *_args: Any, **_kwargs: Any) -> Any:
        calls.append((op, dict(params)))
        return _op_result({"schema": "available"})

    spec = _op("query.sql_schema", Verb.FIND, "read")
    spec.effect = Effect.READ
    projection = _projection(Registry(spec), invoke=invoke)

    executed = asyncio.run(
        dispatch_verb(
            "find", projection, op="query.sql_schema", params={"value": "users"}
        )
    )
    assert executed["result"] == {"schema": "available"}
    assert calls == [("query.sql_schema", {"value": "users"})]

    preview = asyncio.run(
        dispatch_verb("find", projection, op="query.sql_schema", execute=False)
    )
    assert preview["ok"] is True
    assert calls == [("query.sql_schema", {"value": "users"})]

    other = asyncio.run(
        dispatch_verb(
            "find",
            _projection(Registry(_op("things.read", Verb.ASK, "read"))),
            op="things.read",
        )
    )
    assert other["ok"] is True
    assert calls == [("query.sql_schema", {"value": "users"})]


def test_resources_filter_operation_schemas_by_caller_authority() -> None:
    from graph_os.api.registry import Composite, OpSpec

    permitted = OpSpec(
        id="things.read",
        verb=Verb.ASK,
        summary="Read a thing",
        examples=("Read thing",),
        params=Input,
        result=Output,
        binding=Composite(handler="things.read"),
        scopes=frozenset({"read"}),
    )
    hidden = permitted.model_copy(
        update={"id": "things.secret", "scopes": frozenset({"admin"})}
    )
    registry = Registry(permitted, hidden)

    gate = PolicyGate("none")
    assert asyncio.run(registry_index(registry, Caller(), gate))["ops"] == [
        "things.read"
    ]
    assert (
        asyncio.run(operation_spec(registry, "things.read", Caller(), gate))["spec"][
            "id"
        ]
        == "things.read"
    )
    with pytest.raises(ValueError, match="Unknown GraphOS operation"):
        asyncio.run(operation_spec(registry, "things.secret", Caller(), gate))


def test_unavailable_policy_refuses_discovery_and_nl_resolution() -> None:
    class UnavailableGate:
        async def visible(self, _items: Any, _caller: Any) -> Any:
            raise PolicyUnavailable("PDP unavailable")

    registry = Registry(_op("things.read", Verb.ASK, "read"))
    projection = _projection(registry, policy_gate=UnavailableGate())
    found = asyncio.run(dispatch_verb("find", projection, intent="things"))
    asked = asyncio.run(dispatch_verb("ask", projection, intent="read things"))
    assert found["error"]["code"] == "POLICY_UNAVAILABLE"
    assert asked["error"]["code"] == "POLICY_UNAVAILABLE"
    with pytest.raises(PolicyUnavailable):
        asyncio.run(registry_index(registry, Caller(), UnavailableGate()))


def test_six_tools_share_one_schema_and_digest_in_instructions() -> None:
    from graph_os.api.mcp.verbs import mcp_instructions

    projection = _projection(Registry())
    tools = [
        make_verb(name, projection)
        for name in ("find", "ask", "why", "write", "act", "manage")
    ]
    assert {tool.name for tool in tools} == {
        "find",
        "ask",
        "why",
        "write",
        "act",
        "manage",
    }
    assert all(tool.parameters == PARAMETERS for tool in tools)
    assert "registry_digest=digest-one" in mcp_instructions(projection)
