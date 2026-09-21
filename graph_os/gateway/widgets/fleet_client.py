"""Connector-widget calls through the one served MCP fleet authority.

Widgets own display projection only.  Connector availability, credentials,
transport policy, and execution stay behind the EG-backed fleet catalog and
the multiplexer that GraphOS is already serving.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from graph_os.fleet.multiplexer import MCPMultiplexer
from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer

_VERBS = (
    "get",
    "list",
    "create",
    "update",
    "delete",
    "search",
    "read",
    "fetch",
    "query",
)
_IGNORED_INPUTS = frozenset(
    {"action", "params", "params_json", "ctx", "context", "allow_destructive"}
)
_SERVER_OVERRIDES = {
    "atlassian": "atlassian-agent",
    "ciso_assistant": "ciso-assistant-api",
    "clarity": "clarity-api",
    "dockerhub": "dockerhub-api",
    "erpnext": "erpnext-agent",
    "fan_manager": "fan-manager",
    "freshrss": "freshrss-agent",
    "github": "github-agent",
    "gitlab": "gitlab-api",
    "home_assistant": "home-assistant-agent",
    "keycloak": "keycloak-agent",
    "langfuse": "langfuse-agent",
    "leanix": "leanix-agent",
    "listmonk": "listmonk-api",
    "microsoft": "microsoft-agent",
    "nextcloud": "nextcloud-agent",
    "okta": "okta-agent",
    "onetrust": "onetrust-api",
    "owncast": "owncast-agent",
    "plane": "plane-agent",
    "portainer": "portainer-agent",
    "postiz": "postiz-agent",
    "qbittorrent": "qbittorrent-agent",
    "repository_manager": "repository-manager",
    "rom_manager": "rom-manager",
    "servicenow": "servicenow-api",
    "technitium": "technitium-dns-mcp",
    "tunnel_manager": "tunnel-manager",
    "uptime_kuma": "uptime-kuma-agent",
    "wger": "wger-agent",
}


class FleetConnectorUnavailable(RuntimeError):
    """The catalog has no admitted connector tool for a widget operation."""


def count_items(value: Any, key: str = "results", *, depth: int = 3) -> int:
    """Best-effort count for the small list-shaped widget projections."""
    if depth <= 0:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for candidate_key in (key, "data", "results"):
            if candidate_key in value:
                return count_items(value[candidate_key], key, depth=depth - 1)
        return 0
    if hasattr(value, key):
        return count_items(getattr(value, key), key, depth=depth - 1)
    return 0


@dataclass(frozen=True, slots=True)
class _ToolRoute:
    name: str
    action: str | None
    schema: Mapping[str, Any]


def _server_name(service_type: str) -> str:
    return _SERVER_OVERRIDES.get(service_type, f"{service_type.replace('_', '-')}-mcp")


def _input_schema(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    value = tool.get("inputSchema", tool.get("input_schema", {}))
    return value if isinstance(value, Mapping) else {}


def _properties(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    value = schema.get("properties", {})
    return value if isinstance(value, Mapping) else {}


def _action_values(schema: Mapping[str, Any]) -> tuple[str, ...]:
    action = _properties(schema).get("action", {})
    if not isinstance(action, Mapping):
        return ()
    values = action.get("enum", ())
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        return tuple(value for value in values if isinstance(value, str))
    return ()


def _method_candidates(method: str) -> tuple[str, ...]:
    candidates = [method]
    for verb in _VERBS:
        prefix = f"{verb}_"
        if method.startswith(prefix):
            candidates.extend((verb, method.removeprefix(prefix)))
            break
    return tuple(dict.fromkeys(candidates))


def _name_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def _route_score(tool_name: str, method: str, action: str | None) -> tuple[int, int]:
    method_tokens = _name_tokens(method) - set(_VERBS)
    tool_tokens = _name_tokens(tool_name)
    overlap = len(method_tokens & tool_tokens)
    exact_tool = int(tool_name == method or tool_name.endswith(f"_{method}"))
    exact_action = int(action == method)
    return (exact_action * 10 + exact_tool * 8 + overlap * 2, -len(tool_name))


def _route_for_tool(
    tool: Mapping[str, Any], method: str, candidates: tuple[str, ...]
) -> tuple[tuple[int, int], _ToolRoute] | None:
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        return None
    schema = _input_schema(tool)
    matching_actions = [item for item in candidates if item in _action_values(schema)]
    if matching_actions:
        action = matching_actions[0]
        return _route_score(name, method, action), _ToolRoute(name, action, schema)
    if name == method or name.endswith(f"_{method}"):
        return _route_score(name, method, None), _ToolRoute(name, None, schema)
    return None


def _select_route(tools: Sequence[Mapping[str, Any]], method: str) -> _ToolRoute:
    candidates = _method_candidates(method)
    routes = [
        route
        for tool in tools
        if (route := _route_for_tool(tool, method, candidates)) is not None
    ]
    if not routes:
        raise FleetConnectorUnavailable(
            "connector operation is not in the fleet catalog"
        )
    routes.sort(key=lambda item: item[0], reverse=True)
    best_score = routes[0][0]
    best = [route for score, route in routes if score == best_score]
    if len(best) != 1:
        raise FleetConnectorUnavailable(
            "connector operation is ambiguous in the fleet catalog"
        )
    return best[0]


def _positional_arguments(
    schema: Mapping[str, Any], values: Sequence[Any]
) -> dict[str, Any]:
    names = [name for name in _properties(schema) if name not in _IGNORED_INPUTS]
    if len(values) > len(names):
        raise TypeError("too many positional connector arguments")
    return dict(zip(names, values, strict=False))


def _wire_arguments(
    route: _ToolRoute, positional: Sequence[Any], keyword: Mapping[str, Any]
) -> dict[str, Any]:
    params = _positional_arguments(route.schema, positional)
    params.update(keyword)
    if route.action is None:
        return params
    properties = _properties(route.schema)
    arguments: dict[str, Any] = {"action": route.action}
    if "params_json" in properties:
        arguments["params_json"] = json.dumps(
            params, sort_keys=True, separators=(",", ":")
        )
    elif "params" in properties:
        arguments["params"] = params
    else:
        arguments.update(params)
    return arguments


async def _invoke(
    server_name: str,
    method: str,
    positional: Sequence[Any],
    keyword: Mapping[str, Any],
    *,
    timeout: float,
) -> Any:
    async def call(mux: MCPMultiplexer) -> Any:
        tools = await mux.delegated_server_tools(server_name)
        route = _select_route(tools, method)
        return await mux.delegate_server_tool(
            server_name=server_name,
            tool_name=route.name,
            arguments=_wire_arguments(route, positional, keyword),
            timeout=timeout,
        )

    return await run_on_served_multiplexer(call)


def _run(operation: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Execute from the aggregator's worker thread, never the served loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(operation())
    raise RuntimeError("connector widgets must execute in the aggregator worker pool")


class FleetConnectorClient:
    """Small sync projection over the served multiplexer's async tool boundary."""

    def __init__(self, service_type: str, *, timeout: float = 30.0) -> None:
        self._server_name = _server_name(service_type)
        self._timeout = timeout

    def __getattr__(self, method: str) -> Callable[..., Any]:
        if method.startswith("_"):
            raise AttributeError(method)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            return _run(
                lambda: _invoke(
                    self._server_name,
                    method,
                    args,
                    kwargs,
                    timeout=self._timeout,
                )
            )

        return invoke
