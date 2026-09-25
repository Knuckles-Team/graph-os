"""Focused MCPI-05 projection checks; server cutover is covered separately."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from graph_os.api.registry import Surface, Verb
from pydantic import BaseModel

from graph_os.api.mcp.discovery import find_visible
from graph_os.api.mcp.resources import operation_spec, registry_index
from graph_os.api.mcp.verbs import PARAMETERS, MCPProjection, dispatch_verb, make_verb


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
    effective_scopes: frozenset[str] = frozenset({"read", "write"})
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


def _projection(
    registry: Registry, *, policy: Any = lambda _op, _caller: True, fleet: Any = None
) -> MCPProjection:
    return MCPProjection(registry, object(), Resolver(), Caller, policy, fleet)


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
            policy=lambda _op, _caller: True,
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


def test_direct_verb_mismatch_refuses_before_invocation() -> None:
    registry = Registry(_op("things.update", Verb.WRITE, "write"))
    result = asyncio.run(
        dispatch_verb(
            "ask", _projection(registry), op="things.update", params={"value": "x"}
        )
    )
    assert result["error"]["code"] == "VERB_MISMATCH"


def test_natural_language_write_uses_preview_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph_os.api.invoke as invoke_module

    received: dict[str, Any] = {}

    async def invoke(*_args: Any, **kwargs: Any) -> Any:
        received.update(kwargs)
        return invoke_module.OpResult(
            code="CONFIRMATION_REQUIRED", details={"plan_ref": "bound-plan"}
        )

    monkeypatch.setattr(invoke_module, "invoke", invoke)
    registry = Registry(_op("things.update", Verb.WRITE, "write"))
    result = asyncio.run(
        dispatch_verb(
            "write",
            _projection(registry),
            intent="update thing",
            params={"value": "new"},
        )
    )
    assert received["resolved_from_intent"] is True
    assert result["meta"]["plan"]["plan_ref"] == "bound-plan"


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

    def policy(_op: Any, _caller: Any) -> bool:
        return True

    assert registry_index(registry, Caller(), policy)["ops"] == ["things.read"]
    assert (
        operation_spec(registry, "things.read", Caller(), policy)["spec"]["id"]
        == "things.read"
    )
    with pytest.raises(ValueError, match="Unknown GraphOS operation"):
        operation_spec(registry, "things.secret", Caller(), policy)


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
