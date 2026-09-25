"""EH-629: the served Eunomia filter narrows list and call identically, fails closed."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from eunomia_core import schemas
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from graph_os.mcp_server import policy_filter, runtime
from graph_os.mcp_server.policy_decision import RemotePolicy, evaluate_policies
from graph_os.mcp_server.policy_filter import (
    ServedPolicyFilter,
    authorize_native_call,
    build_policy_filter,
    install_policy_filter,
)
from tests.fleet.conftest import fleet_session

TOOLS = ("allowed_tool", "list_only_tool", "denied_tool")


def _rule(name: str, tools: list[str], actions: list[str]) -> dict:
    return {
        "name": name,
        "effect": "allow",
        "principal_conditions": [
            {"path": "uri", "operator": "equals", "value": "agent:fleet-test"}
        ],
        "resource_conditions": [
            {"path": "attributes.name", "operator": "in", "value": tools}
        ],
        "actions": actions,
    }


def _policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "name": "eh-629",
                "default_effect": "deny",
                "rules": [
                    _rule("both", ["allowed_tool"], ["list", "execute"]),
                    _rule("list-only", ["list_only_tool"], ["list"]),
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _embedded(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        eunomia_type="embedded", eunomia_policy_file=str(_policy_file(tmp_path))
    )


def _server(policy: ServedPolicyFilter | None) -> FastMCP:
    assert policy is not None
    mcp = FastMCP("policy-filter")
    for name in TOOLS:
        mcp.tool(name=name)(lambda: "ran")
    mcp.add_middleware(policy)
    return mcp


@pytest.fixture(autouse=True)
def _no_installed_policy() -> Iterator[None]:
    yield
    policy_filter._ACTIVE.clear()


async def _called(client: Client, name: str) -> bool:
    try:
        await client.call_tool(name, {})
    except ToolError as exc:
        assert "POLICY_DENIED" in str(exc)
        return False
    return True


@pytest.mark.parametrize("source", ["embedded", "remote"])
async def test_list_and_call_agree_for_every_tool(tmp_path, source: str) -> None:
    """Embedded policy file or reachable remote PDP: list == callable."""
    policy = (
        build_policy_filter(_embedded(tmp_path))
        if source == "embedded"
        else ServedPolicyFilter(_remote_pdp({"allowed_tool"}))
    )
    mcp = _server(policy)
    with fleet_session():
        async with Client(mcp) as client:
            listed = {tool.name for tool in await client.list_tools()}
            callable_ = {name for name in TOOLS if await _called(client, name)}
    assert listed == callable_ == {"allowed_tool"}


async def test_no_verified_caller_sees_and_calls_nothing(tmp_path) -> None:
    mcp = _server(build_policy_filter(_embedded(tmp_path)))
    async with Client(mcp) as client:
        assert await client.list_tools() == []
        with pytest.raises(ToolError, match="POLICY_DENIED"):
            await client.call_tool("allowed_tool", {})


async def test_unreachable_policy_service_fails_closed() -> None:
    config = SimpleNamespace(
        eunomia_type="remote",
        eunomia_remote_url="http://127.0.0.1:9",
        eunomia_api_key_ref=None,
    )
    mcp = _server(build_policy_filter(config))
    with fleet_session():
        async with Client(mcp) as client:
            assert await client.list_tools() == []
            with pytest.raises(ToolError, match="POLICY_UNAVAILABLE"):
                await client.call_tool("allowed_tool", {})


async def test_eunomia_off_installs_no_filter_and_native_calls_pass() -> None:
    assert install_policy_filter(SimpleNamespace(eunomia_type="none")) is None
    await authorize_native_call("any_tool")  # no policy: EG enforces authority


@pytest.mark.parametrize(
    "config",
    [
        SimpleNamespace(eunomia_type="bogus"),
        SimpleNamespace(eunomia_type="embedded", eunomia_policy_file="/nonexistent"),
        SimpleNamespace(
            eunomia_type="remote", eunomia_remote_url=None, eunomia_api_key_ref=None
        ),
    ],
)
def test_misconfigured_eunomia_stops_startup(config) -> None:
    with pytest.raises(ValueError):
        build_policy_filter(config)


async def test_native_rest_and_delegation_calls_are_filtered(
    tmp_path, monkeypatch
) -> None:
    async def tool() -> str:
        return "ran"

    for name in TOOLS:
        monkeypatch.setitem(runtime.REGISTERED_TOOLS, name, tool)
    install_policy_filter(_embedded(tmp_path))
    with fleet_session():
        with pytest.raises(ToolError, match="POLICY_DENIED"):
            await runtime._execute_tool("list_only_tool")
        with pytest.raises(ToolError, match="POLICY_DENIED"):
            await runtime._execute_tool("denied_tool")
    install_policy_filter(SimpleNamespace(eunomia_type="none"))
    with fleet_session():
        await authorize_native_call("denied_tool")


@pytest.mark.parametrize("enabled", [True, False])
def test_served_server_installs_the_filter_only_when_enabled(
    tmp_path, monkeypatch, enabled: bool
) -> None:
    from agent_utilities.core.config import config

    mode = "embedded" if enabled else "none"
    monkeypatch.setattr(config, "eunomia_type", mode)
    monkeypatch.setattr(config, "eunomia_policy_file", str(_policy_file(tmp_path)))
    _args, _mcp, middlewares = runtime._build_server(bootstrap=False)
    names = [type(middleware).__name__ for middleware in middlewares]
    expected_tail = ["VerifiedSessionMiddleware"] + (
        ["ServedPolicyFilter"] if enabled else []
    )
    assert names[-len(expected_tail) :] == expected_tail
    assert bool(policy_filter._ACTIVE) is enabled


def _remote_pdp(allowed_names: set[str]) -> RemotePolicy:
    def handler(request: httpx.Request) -> httpx.Response:
        checks = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {"allowed": check["resource"]["attributes"]["name"] in allowed_names}
                for check in checks
            ],
        )

    return RemotePolicy(
        "https://pdp.example.test", SimpleNamespace(), httpx.MockTransport(handler)
    )


def test_explicit_deny_beats_allow_and_default_never_allows() -> None:
    request = schemas.CheckRequest(
        principal=schemas.PrincipalCheck(uri="agent:fleet-test"),
        resource=schemas.ResourceCheck(uri="mcp:tools:t", attributes={"name": "t"}),
        action="execute",
    )
    allow = schemas.Policy.model_validate(
        {
            "name": "a",
            "default_effect": "allow",
            "rules": [_rule("a", ["t"], ["execute"])],
        }
    )
    deny_rule = {**_rule("d", ["t"], ["execute"]), "effect": "deny"}
    deny = schemas.Policy.model_validate({"name": "d", "rules": [deny_rule]})
    empty = schemas.Policy.model_validate(
        {"name": "e", "default_effect": "allow", "rules": []}
    )
    assert evaluate_policies([allow], request).allowed is True
    assert evaluate_policies([allow, deny], request).allowed is False
    assert evaluate_policies([empty], request).allowed is False
