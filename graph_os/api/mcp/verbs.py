"""Six small MCP intent tools over the shared operation chokepoint."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.mcp.discovery import (
    FleetSearch,
    describe_op,
    find_visible,
    visible_ops,
)

VERBS = ("find", "ask", "why", "write", "act", "manage")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"type": "string"},
        "intent": {"type": "string"},
        "params": {"type": "object", "additionalProperties": True},
        "plan_ref": {"type": "string"},
        "execute": {"type": "boolean"},
        "idempotency_key": {"type": "string"},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class MCPProjection:
    """Dependencies supplied by the single GraphOS server composition root."""

    registry: Any
    services: Any
    resolver: Any
    caller_for_request: Callable[[], Any]
    policy_gate: Any
    fleet_search: FleetSearch | None = None


def _envelope(
    value: Any, registry: Any, *, meta: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": value,
        "meta": {
            "registry_digest": registry.digest,
            "api_version": "v1",
            **(meta or {}),
        },
    }


def _refusal(
    code: str,
    projection: MCPProjection,
    caller: Any,
    op_id: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal, to_envelope

    _, envelope = to_envelope(
        GraphOSRefusal(GraphOSErrorCode(code), details=details or {}),
        op=op_id or "find",
        request_id=getattr(caller, "request_id", "") or "unassigned",
        registry_digest=projection.registry.digest,
    )
    return envelope


def _scope_ref(projection: MCPProjection, caller: Any) -> str:
    return projection.resolver.scope_ref(
        caller.tenant,
        caller.policy_revision or "default",
        scopes=tuple(sorted(caller.effective_scopes)),
    )


async def dispatch_verb(
    verb: str,
    projection: MCPProjection,
    *,
    op: str | None = None,
    intent: str | None = None,
    params: Mapping[str, Any] | None = None,
    plan_ref: str | None = None,
    execute: bool = True,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Route one verb without granting authority through a client hint."""

    from graph_os.api.invoke import OpError, invoke
    from graph_os.api.policy import PolicyUnavailable
    from graph_os.api.registry import Surface

    if verb not in VERBS:
        raise ValueError("unknown MCP intent verb")
    caller = projection.caller_for_request()
    if caller is None or not caller.authenticated:
        return _refusal("UNAUTHENTICATED", projection, caller, op or verb)
    arguments = params or {}
    if not isinstance(arguments, Mapping):
        return _refusal("INVALID_ARGUMENT", projection, caller, op or verb)
    scope_ref = _scope_ref(projection, caller)
    if verb == "find":
        try:
            found = await find_visible(
                registry=projection.registry,
                caller=caller,
                policy_gate=projection.policy_gate,
                resolver=projection.resolver,
                fleet_search=projection.fleet_search,
                intent=intent,
                op=op,
                params=arguments,
                scope_ref=scope_ref,
            )
        except PolicyUnavailable:
            return _refusal("POLICY_UNAVAILABLE", projection, caller, op or verb)
        return _envelope(found, projection.registry)
    if not op and not intent:
        return _refusal("INVALID_ARGUMENT", projection, caller, verb)
    resolution = None
    if not op:
        try:
            visible = await visible_ops(
                projection.registry, caller, projection.policy_gate, verb=verb
            )
        except PolicyUnavailable:
            return _refusal("POLICY_UNAVAILABLE", projection, caller, verb)
        candidates = [describe_op(item) for item in visible]
        resolution = projection.resolver.resolve(
            verb, intent or "", candidates, scope_ref=scope_ref, params=arguments
        )
        op = resolution.op
        if not op:
            return _refusal("UNKNOWN_OP", projection, caller, verb)
        arguments = resolution.params
        if resolution.missing_required:
            return _envelope(
                {
                    "preview": True,
                    "op": op,
                    "params": arguments,
                    "missing_required": list(resolution.missing_required),
                },
                projection.registry,
                meta={
                    "resolved_op": op,
                    "alternatives": list(resolution.alternatives),
                    "why": resolution.why,
                },
            )
    selected = projection.registry.get(op)
    if selected is None:
        return _refusal("UNKNOWN_OP", projection, caller, op)
    if selected.verb.value != verb:
        return _refusal("VERB_MISMATCH", projection, caller, op)
    if not execute:
        return _envelope(
            {
                "preview": True,
                "op": op,
                "params": arguments,
                "spec": describe_op(selected),
            },
            projection.registry,
        )
    outcome = await invoke(
        op,
        arguments,
        caller,
        Surface.MCP,
        services=projection.services,
        plan_ref=plan_ref,
        idempotency_key=idempotency_key,
        resolved_from_intent=resolution is not None,
    )
    if isinstance(outcome, OpError):
        return _refusal(outcome.code, projection, caller, op, outcome.details)
    meta = {}
    if resolution is not None:
        meta = {
            "resolved_op": op,
            "alternatives": list(resolution.alternatives),
            "why": resolution.why,
        }
    if outcome.code != "OK":
        meta["plan"] = dict(outcome.details)
    return _envelope(outcome.value, projection.registry, meta=meta)


def make_verb(name: str, projection: MCPProjection) -> Any:
    """Construct a FastMCP tool with the same stable schema for every verb."""

    from fastmcp.tools import FunctionTool, ToolResult

    from mcp.types import TextContent

    if name not in VERBS:
        raise ValueError("unknown MCP intent verb")

    async def _call(
        op: str | None = None,
        intent: str | None = None,
        params: dict[str, Any] | None = None,
        plan_ref: str | None = None,
        execute: bool = True,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        payload = await dispatch_verb(
            name,
            projection,
            op=op,
            intent=intent,
            params=params,
            plan_ref=plan_ref,
            execute=execute,
            idempotency_key=idempotency_key,
        )
        summary = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return ToolResult(
            content=[TextContent(type="text", text=summary[:1000])],
            structured_content=payload,
        )

    return FunctionTool(
        name=name,
        description=f"GraphOS {name} intent over the authorized operation registry",
        parameters=PARAMETERS,
        fn=_call,
    )


def mcp_instructions(projection: MCPProjection) -> str:
    """Advertise the schema refresh key and stable intent surface at initialize."""

    return (
        "GraphOS exposes find, ask, why, write, act, manage and four fleet tools. "
        "Use find or graphos://registry for operation schemas. "
        f"registry_digest={projection.registry.digest}"
    )
