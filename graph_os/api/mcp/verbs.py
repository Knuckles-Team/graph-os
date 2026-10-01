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
from graph_os.api.registry import Invoke

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
    invoke: Invoke
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


def _is_op_error(outcome: Any) -> bool:
    """Tell a refusal from a result without importing the invocation pipeline.

    ``invoke`` (GRAPHOS-OPS-R007) returns either an ``OpResult``, which
    always carries a ``value``, or an ``OpError``, which never does; a
    result with a non-``OK`` code (for example a bound confirmation plan)
    still reports through the result path below, matching the resident
    verbs' own preview handling.
    """

    return not hasattr(outcome, "value")


def _scope_ref(projection: MCPProjection, caller: Any) -> str:
    return projection.resolver.scope_ref(
        caller.tenant,
        caller.policy_revision or "default",
        scopes=tuple(sorted(caller.effective_scopes)),
    )


def _authenticated_arguments(
    verb: str,
    projection: MCPProjection,
    op: str | None,
    params: Mapping[str, Any] | None,
) -> tuple[Any, Mapping[str, Any], dict[str, Any] | None]:
    """Return the caller and arguments, or a refusal envelope in their place."""

    if verb not in VERBS:
        raise ValueError("unknown MCP intent verb")
    caller = projection.caller_for_request()
    if caller is None or not caller.authenticated:
        return caller, {}, _refusal("UNAUTHENTICATED", projection, caller, op or verb)
    arguments = params or {}
    if not isinstance(arguments, Mapping):
        return caller, {}, _refusal("INVALID_ARGUMENT", projection, caller, op or verb)
    return caller, arguments, None


async def _try_find_discovery(
    verb: str,
    projection: MCPProjection,
    caller: Any,
    *,
    op: str | None,
    intent: str | None,
    arguments: Mapping[str, Any],
    scope_ref: str,
    execute: bool,
) -> dict[str, Any] | None:
    """Return the `find` discovery envelope, or None to invoke a single op.

    `find` is both the discovery tool and an operation verb. An exact `find`
    op with execute=true uses the same governed invoke path as the other
    five verbs; discovery and exact schema previews stay read-only.
    """

    from graph_os.api.policy import PolicyUnavailable

    selected = projection.registry.get(op) if op and verb == "find" else None
    exact_find = selected is not None and selected.verb.value == "find"
    if verb != "find" or (exact_find and execute):
        return None
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


async def _resolve_op(
    verb: str,
    projection: MCPProjection,
    caller: Any,
    *,
    op: str | None,
    intent: str | None,
    scope_ref: str,
    arguments: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], Any, dict[str, Any] | None]:
    """Settle on a concrete op id, from an exact op or a natural-language intent."""

    from graph_os.api.policy import PolicyUnavailable

    if op:
        return op, arguments, None, None
    if not intent:
        return (
            "",
            arguments,
            None,
            _refusal("INVALID_ARGUMENT", projection, caller, verb),
        )
    try:
        visible = await visible_ops(
            projection.registry, caller, projection.policy_gate, verb=verb
        )
    except PolicyUnavailable:
        return (
            "",
            arguments,
            None,
            _refusal("POLICY_UNAVAILABLE", projection, caller, verb),
        )
    candidates = [describe_op(item) for item in visible]
    resolution = projection.resolver.resolve(
        verb, intent, candidates, scope_ref=scope_ref, params=arguments
    )
    resolved_op = resolution.op
    if not resolved_op:
        return "", arguments, None, _refusal("UNKNOWN_OP", projection, caller, verb)
    resolved_arguments = resolution.params
    if not resolution.missing_required:
        return resolved_op, resolved_arguments, resolution, None
    preview = _envelope(
        {
            "preview": True,
            "op": resolved_op,
            "params": resolved_arguments,
            "missing_required": list(resolution.missing_required),
        },
        projection.registry,
        meta={
            "resolved_op": resolved_op,
            "alternatives": list(resolution.alternatives),
            "why": resolution.why,
        },
    )
    return resolved_op, resolved_arguments, resolution, preview


async def _invoke_and_render(
    projection: MCPProjection,
    caller: Any,
    op: str,
    arguments: Mapping[str, Any],
    *,
    plan_ref: str | None,
    idempotency_key: str | None,
    resolution: Any,
) -> dict[str, Any]:
    """Call the shared invocation chokepoint and project its outcome."""

    from graph_os.api.registry import Surface

    outcome = await projection.invoke(
        op,
        arguments,
        caller,
        Surface.MCP,
        services=projection.services,
        plan_ref=plan_ref,
        idempotency_key=idempotency_key,
        resolved_from_intent=resolution is not None,
    )
    if _is_op_error(outcome):
        from graph_os.api.errors import to_envelope

        _, envelope = to_envelope(
            outcome,
            op=op,
            request_id=getattr(caller, "request_id", "") or "unassigned",
            registry_digest=projection.registry.digest,
        )
        return envelope
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

    caller, arguments, refusal = _authenticated_arguments(verb, projection, op, params)
    if refusal is not None:
        return refusal
    scope_ref = _scope_ref(projection, caller)
    found = await _try_find_discovery(
        verb,
        projection,
        caller,
        op=op,
        intent=intent,
        arguments=arguments,
        scope_ref=scope_ref,
        execute=execute,
    )
    if found is not None:
        return found
    op, arguments, resolution, refusal = await _resolve_op(
        verb,
        projection,
        caller,
        op=op,
        intent=intent,
        scope_ref=scope_ref,
        arguments=arguments,
    )
    if refusal is not None:
        return refusal
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
    return await _invoke_and_render(
        projection,
        caller,
        op,
        arguments,
        plan_ref=plan_ref,
        idempotency_key=idempotency_key,
        resolution=resolution,
    )


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
