"""``graph_policy_release``: the operator MCP/REST surface over the pointer."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.control_plane.policy_evolution import PolicyEvolutionControlError
from graph_os.mcp_server import runtime
from graph_os.mcp_server.policy_release import (
    PolicyReleaseRequest,
    register_policy_release_tools,
)
from tests.control_plane.policy_evolution.eg_fakes import FakeNodes
from tests.control_plane.policy_evolution.test_policy_evolution import (
    CAP,
    EVAL,
    SCOPE,
    V1,
    V2,
    _evaluation,
    _records,
)


class _Mcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, name: str, **_kwargs: Any) -> Any:
        def decorate(function: Any) -> Any:
            self.tools[name] = function
            return function

        return decorate


class _Client:
    def __init__(self) -> None:
        self.nodes = FakeNodes()
        self.policy_evolution = _records()
        self.policy_evolution.put(
            EVAL + "0", "policy_evaluation", _evaluation(version=V1, baseline=None)
        )
        self.bound: list[Any] = []

    @contextlib.contextmanager
    def use_verified_context(self, claims: Any) -> Any:
        self.bound.append(claims)
        yield


def _session(scopes: tuple[str, ...]) -> Any:
    return SimpleNamespace(
        tenant="tenant-a",
        scopes=scopes,
        engine_verified_context=lambda: {"tenant": "tenant-a"},
    )


@pytest.fixture
def surface(monkeypatch: pytest.MonkeyPatch) -> Any:
    client = _Client()
    state = {"scopes": (SCOPE,)}

    @contextlib.contextmanager
    def scope() -> Any:
        yield _session(state["scopes"])

    monkeypatch.setattr(runtime, "verified_tool_session_scope", scope)
    monkeypatch.setattr(runtime, "graph_client", lambda tenant: client)
    mcp = _Mcp()
    prior = runtime.REGISTERED_TOOLS.get("graph_policy_release")
    register_policy_release_tools(mcp)
    registered = mcp.tools["graph_policy_release"]

    async def tool(**fields: Any) -> str:
        return str(await registered(PolicyReleaseRequest(**fields)))

    yield SimpleNamespace(tool=tool, client=client, state=state)
    runtime.REGISTERED_TOOLS.pop("graph_policy_release", None)
    if prior is not None:
        runtime.REGISTERED_TOOLS["graph_policy_release"] = prior


def _move(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "family": "qwen-policy",
        "channel": "stable",
        "capability_id": CAP,
        "change_ref": "change-1",
    }
    values.update(overrides)
    return values


async def test_status_promote_rollback_round_trip(surface: Any) -> None:
    tool = surface.tool
    assert await tool(action="status", family="qwen-policy", channel="stable") == "null"
    first = json.loads(
        await tool(
            action="promote", **_move(next_version_id=V1, evaluation_id=EVAL + "0")
        )
    )
    assert (first["version_id"], first["revision"]) == (V1, 1)
    second = json.loads(
        await tool(
            action="promote",
            **_move(
                expected_revision=1,
                expected_version_id=V1,
                next_version_id=V2,
                evaluation_id=EVAL,
            ),
        )
    )
    assert (second["version_id"], second["previous_version_id"]) == (V2, V1)
    back = json.loads(
        await tool(
            action="rollback",
            **_move(expected_revision=2, expected_version_id=V2, next_version_id=V1),
        )
    )
    assert back["version_id"] == V1
    status = json.loads(
        await tool(action="status", family="qwen-policy", channel="stable")
    )
    assert status["revision"] == 3
    assert surface.client.bound and all(
        c == {"tenant": "tenant-a"} for c in surface.client.bound
    )


async def test_a_session_without_the_capability_scope_cannot_move(surface: Any) -> None:
    surface.state["scopes"] = ("kg:read",)
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await surface.tool(
            action="promote", **_move(next_version_id=V1, evaluation_id=EVAL + "0")
        )
    assert refused.value.code == "POLICY_SCOPE_NOT_GRANTED"


def test_release_pointer_is_declared_as_governed_operations() -> None:
    from graph_os.api.ops.policy import specs

    assert {
        "policy.release.status",
        "policy.release.promote",
        "policy.release.rollback",
    }.issubset({op.id for op in specs()})
