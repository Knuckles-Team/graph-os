"""Bind one governed fleet to the served operation API and resident tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.invoke import OpError, OpResult, invoke
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.policy import PolicyGate, fleet_resource
from graph_os.api.registry import Surface
from graph_os.fleet.catalog_composition import compose_multiplexer_ops
from graph_os.fleet.catalog_items import CatalogItem
from graph_os.fleet.catalog_sources import SdkRead
from graph_os.fleet.gateway_ops import (
    FleetGateway,
    annotation_effect,
    oauth_delegated_call_for_mux,
    tool_for_multiplexer_ops,
)


class _PendingFleet:
    """Refuse every fleet call until the single MCP multiplexer is attached."""

    def __init__(self) -> None:
        self.ops: Any = None
        self.mux: Any = None

    def require_ops(self) -> Any:
        if self.ops is None:
            raise RuntimeError("governed fleet operations are not attached")
        return self.ops

    def require_mux(self) -> Any:
        if self.mux is None:
            raise RuntimeError("governed fleet multiplexer is not attached")
        return self.mux

    def bind(self, mux: Any, ops: Any) -> None:
        if self.ops is not None or self.mux is not None:
            raise RuntimeError("governed fleet is already attached")
        self.mux = mux
        self.ops = ops


class _CatalogOps:
    def __init__(self, pending: _PendingFleet) -> None:
        self._pending = pending

    def __getattr__(self, name: str) -> Any:
        if name not in {"search", "list", "status", "load", "unload"}:
            raise AttributeError(name)
        return getattr(self._pending.require_ops(), name)


@dataclass(frozen=True, slots=True)
class FleetHostPorts:
    gateway: FleetGateway
    search: Callable[..., Awaitable[list[Mapping[str, Any]]]]
    ops_factory: Callable[..., Any]
    resolver: IntentResolver


def _resource(item: CatalogItem) -> Any:
    kind = "connector" if item.kind == "connector_item" else item.kind
    if kind == "resource_template":
        kind = "resource"
    if kind not in {"tool", "prompt", "resource", "skill", "connector"}:
        raise ValueError("fleet item kind has no policy resource")
    effect = "read"
    principal = "any"
    if kind == "tool":
        classified, _, rule = annotation_effect(
            item.annotations, override=item.effect_override
        )
        effect = classified.value
        principal = rule.value
    return fleet_resource(
        kind,
        f"{item.server}/{item.name}" if item.server else item.name,
        required_scopes=item.required_scopes,
        effect=effect,
        principal_rule=principal,
    )


def compose_fleet_host_ports(
    *,
    reader: Any,
    sdk_entries: SdkRead,
    policy_gate: PolicyGate,
    read_item: Callable[..., Awaitable[Any]] | None = None,
) -> FleetHostPorts:
    """Compose a caller-filtered fleet from explicit EG and SDK authorities.

    The deferred gateway exists before FastMCP construction, but refuses calls
    until ``attach_fleet_loader`` invokes the returned factory exactly once.
    Its catalog source is the process's verified EG reader plus an explicit
    SDK pack reader; no static or empty stand-in is inferred here.
    """

    if reader is None or not callable(getattr(reader, "read", None)):
        raise ValueError("verified EG fleet catalog reader is required")
    if not callable(sdk_entries):
        raise ValueError("verified SDK pack reader is required")
    if not isinstance(policy_gate, PolicyGate):
        raise ValueError("governed fleet policy is required")
    pending = _PendingFleet()

    async def permitted(item: CatalogItem, caller: Any, action: str) -> bool:
        decision = await policy_gate.decisions([_resource(item)], caller, action)
        return len(decision) == 1 and decision[0] is True

    async def visible(item: CatalogItem, caller: Any) -> bool:
        return await permitted(item, caller, "discover")

    async def loadable(item: CatalogItem, caller: Any) -> bool:
        return await permitted(item, caller, "load")

    async def callable_item(item: CatalogItem, caller: Any) -> bool:
        return await permitted(item, caller, "call")

    async def tool_for(server: str, tool: str, caller: Any) -> Any:
        return await tool_for_multiplexer_ops(pending.require_ops())(
            server, tool, caller
        )

    async def call_policy(server: str, tool: str, caller: Any) -> bool:
        item = await pending.require_ops().admitted_tool(caller, server, tool)
        return await callable_item(item, caller)

    async def delegated_call(
        server: str, tool: str, arguments: Mapping[str, Any], caller: Any
    ) -> Any:
        return await oauth_delegated_call_for_mux(pending.require_mux())(
            server, tool, arguments, caller
        )

    gateway = FleetGateway(
        tool_for=tool_for,
        policy_check=call_policy,
        delegated_call=delegated_call,
        catalog_ops=_CatalogOps(pending),
    )

    async def search(*, caller: Any, **kwargs: Any) -> list[Mapping[str, Any]]:
        result = await pending.require_ops().search(caller, **kwargs)
        items = result.get("items") if isinstance(result, Mapping) else None
        if not isinstance(items, list) or any(
            not isinstance(item, Mapping) for item in items
        ):
            raise RuntimeError("governed fleet search returned no catalog items")
        return items

    async def invoke_fleet(
        op_id: str, params: Mapping[str, Any], caller: Any, surface: str
    ) -> Any:
        if surface != "mcp":
            raise PermissionError("native fleet dispatch requires MCP surface")
        from graph_os.api.serving import configured_served_api

        projection, _, _ = configured_served_api()
        outcome = await invoke(
            op_id, params, caller, Surface.MCP, services=projection.services
        )
        if isinstance(outcome, OpError):
            raise RuntimeError("governed fleet operation refused")
        if not isinstance(outcome, OpResult) or outcome.code != "OK":
            raise RuntimeError("governed fleet operation unavailable")
        return outcome.value

    def session_key_for(caller: Any) -> str:
        session = getattr(caller, "session", None)
        if (
            session is None
            or session.tenant != caller.tenant
            or session.actor.actor_id != caller.principal
        ):
            raise PermissionError("verified fleet session is required")
        from graph_os.fleet.multiplexer import _session_key

        return _session_key()

    def ops_factory(
        mux: Any,
        mount: Any,
        notify: Any,
        native_name: Any,
    ) -> Any:
        if pending.ops is not None:
            raise RuntimeError("governed fleet is already attached")
        ops = compose_multiplexer_ops(
            reader=reader,
            mux=mux,
            sdk_entries=sdk_entries,
            visible=visible,
            loadable=loadable,
            callable_item=callable_item,
            mount=mount,
            notify=notify,
            invoke=invoke_fleet,
            session_key_for=session_key_for,
            native_name=native_name,
            read_item=read_item,
        )
        pending.bind(mux, ops)
        return ops

    return FleetHostPorts(gateway, search, ops_factory, IntentResolver())
