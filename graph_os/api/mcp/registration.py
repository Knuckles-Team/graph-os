"""Prepared ten-tool MCP surface; only the process owner activates registration."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import FunctionTool, ToolResult
from mcp.types import TextContent

from graph_os.api.errors import FleetRefusal
from graph_os.api.mcp.resources import register_resources
from graph_os.api.mcp.verbs import (
    VERBS,
    MCPProjection,
    _invoke_and_render,
    dispatch_verb,
    make_verb,
)
from graph_os.fleet.gateway_ops import native_dispatch_scope
from graph_os.fleet.multiplexer_ops import MultiplexerOps, item_binding
from graph_os.fleet.session_loads import LoadGrant

FLEET_OPERATIONS = {
    "find_tools": "fleet.catalog.search",
    "load_tools": "fleet.tools.load",
    "unload_tools": "fleet.tools.unload",
    "multiplexer_status": "fleet.status",
}
RESIDENT_NAMES = (*VERBS, *FLEET_OPERATIONS)
_AUTHORITY_REFUSALS = frozenset(
    {
        "UNAUTHENTICATED",
        "PRINCIPAL_NOT_ALLOWED",
        "SCOPE_REQUIRED",
        "POLICY_DENIED",
        "POLICY_UNAVAILABLE",
        "SUBJECT_ACCESS_DENIED",
        "UNKNOWN_TOOL",
    }
)


def _tool_result(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload, sort_keys=True)[:1000])
        ],
        structured_content=payload,
    )


def _native_result(payload: dict[str, Any]) -> ToolResult:
    """Preserve the MCP wire result carried by the fleet operation result."""
    from graph_os.fleet.multiplexer import _tool_result_from_child, mcp_types

    result = payload["result"]
    child = result.get("value") if isinstance(result, Mapping) else None
    if not isinstance(child, Mapping) or "content" not in child:
        return _tool_result(payload)
    wire = mcp_types.CallToolResult.model_validate(child)
    error = getattr(wire, "is_error", None)
    if bool(error if error is not None else getattr(wire, "isError", False)):
        raise ToolError("delegated_child_tool_failed")
    return _tool_result_from_child(wire)


def make_fleet_tool(name: str, projection: MCPProjection) -> FunctionTool:
    """Project existing fleet operation schemas through the shared invocation path."""
    op = projection.registry[FLEET_OPERATIONS[name]]

    async def call(**params: Any) -> ToolResult:
        payload = await dispatch_verb(
            op.verb.value, projection, op=op.id, params=params
        )
        return _tool_result(payload)

    return FunctionTool(
        name=name,
        description=op.summary,
        parameters=op.params.model_json_schema(),
        fn=call,
    )


@dataclass(frozen=True, slots=True)
class FleetMCPBinding:
    """Request-local identity and the one catalog/session service supplied by E.

    The catalog's invoke adapter must already bind the same projection services.
    No cached caller or second session store is created here.
    """

    projection: MCPProjection
    ops: MultiplexerOps
    session_key_for: Callable[[Any], str | None]

    def request_context(self) -> tuple[Any, str]:
        caller = self.projection.caller_for_request()
        if caller is None or caller.authenticated is not True:
            raise ToolError("UNAUTHENTICATED")
        key = self.session_key_for(caller)
        if not isinstance(key, str) or not key:
            raise ToolError("UNAUTHENTICATED")
        return caller, key

    async def visible_names(self) -> frozenset[str]:
        caller, key = self.request_context()
        await self.ops.revoke_invisible(caller, key)
        await self.ops.redeliver_pending(key)
        return frozenset(self.ops.sessions.loaded(key))

    def _dispatch_fence(self, grant: LoadGrant, caller: Any) -> Callable[..., None]:
        def fence(server: str, tool: str, current: Any, commit: bool) -> None:
            target_matches = grant.binding == (grant.item, "tool", server, tool)
            caller_matches = current is None or (
                current.principal == caller.principal
                and current.tenant == caller.tenant
                and self.session_key_for(current) == grant.key
            )
            if not target_matches or not caller_matches:
                raise FleetRefusal("UNKNOWN_TOOL", server, tool)
            accepted = (
                self.ops.sessions.dispatch(grant)
                if commit
                else self.ops.sessions.current(grant)
            )
            if not accepted:
                raise FleetRefusal("UNKNOWN_TOOL", server, tool)

        return fence

    async def _invoke_native(
        self, grant: LoadGrant, caller: Any, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        await self.ops.revoke_invisible(caller, grant.key)
        await self.ops.redeliver_pending(grant.key)
        item = await self.ops.catalog.get(grant.item, caller)
        if (
            item is None
            or item.kind != "tool"
            or not item.server
            or item_binding(item) != grant.binding
            or not self.ops.sessions.current(grant)
        ):
            self.ops.sessions.revoke(grant)
            await self.ops.redeliver_pending(grant.key)
            raise ToolError("UNKNOWN_TOOL")
        with native_dispatch_scope(self._dispatch_fence(grant, caller)):
            payload = await _invoke_and_render(
                self.projection,
                caller,
                "fleet.call",
                {
                    "server": item.server,
                    "tool": item.name,
                    "arguments": dict(arguments),
                },
                plan_ref=None,
                idempotency_key=None,
                resolution=None,
            )
        # Replay/preview has no child dispatch, but still consumes this request's
        # one-shot only if its original load generation remains current.
        if payload["ok"] and not grant.dispatched:
            if not self.ops.sessions.dispatch(grant):
                raise ToolError("UNKNOWN_TOOL")
        return payload

    async def call_native(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        caller, key = self.request_context()
        item_id = self.ops.catalog_id_for_native(name)
        grant = self.ops.sessions.acquire(key, item_id) if item_id else None
        if grant is None:
            raise ToolError("UNKNOWN_TOOL")
        try:
            payload = await self._invoke_native(grant, caller, arguments)
        finally:
            self.ops.sessions.release(grant)
        await self.ops.redeliver_pending(key)
        if not payload["ok"]:
            if payload["error"]["code"] in _AUTHORITY_REFUSALS:
                self.ops.sessions.revoke(grant)
                await self.ops.redeliver_pending(key)
            raise ToolError(json.dumps(payload, sort_keys=True))
        return _native_result(payload)


class GovernedSessionVisibility(Middleware):
    """Route visibility using the catalog service; policy remains in its owner."""

    def __init__(self, binding: FleetMCPBinding) -> None:
        self.binding = binding

    async def on_list_tools(self, context: Any, call_next: Any) -> Any:
        loaded = await self.binding.visible_names()
        tools = await call_next(context)
        return [
            tool
            for tool in tools
            if tool.name in RESIDENT_NAMES
            or self.binding.ops.catalog_id_for_native(tool.name) in loaded
        ]


async def register_mcp_tools(
    mcp: Any, multiplexer: Any, binding: FleetMCPBinding
) -> None:
    """Attach once after E retires legacy tools; attachment failure aborts startup.

    This function is intentionally not called by server/runtime on this lane.
    E must validate live authorities before calling it and must discard the host
    if any attachment fails. Native mounts subsequently use the mux factory.
    """
    if (
        await mcp.list_tools()
        or getattr(multiplexer, "_governed_fleet", None) is not None
    ):
        raise RuntimeError("MCP surface must be empty and unbound before registration")
    if binding.projection.services is None or not callable(binding.projection.invoke):
        raise RuntimeError("shared invocation services are required")
    tools = [make_verb(name, binding.projection) for name in VERBS]
    tools.extend(make_fleet_tool(name, binding.projection) for name in FLEET_OPERATIONS)
    for tool in tools:
        mcp.add_tool(tool)
    names = [tool.name for tool in await mcp.list_tools()]
    if len(names) != len(RESIDENT_NAMES) or set(names) != set(RESIDENT_NAMES):
        raise RuntimeError("MCP resident attachment was incomplete")
    register_resources(
        mcp,
        binding.projection.registry,
        binding.projection.caller_for_request,
        binding.projection.policy_gate,
    )
    mcp.add_middleware(GovernedSessionVisibility(binding))
    multiplexer._governed_fleet = binding
