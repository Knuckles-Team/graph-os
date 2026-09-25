"""Caller-filtered operation and fleet discovery for the MCP ``find`` verb."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

FleetSearch = Callable[..., Awaitable[Sequence[Mapping[str, Any]]]]


async def visible_ops(
    registry: Any, caller: Any, policy_gate: Any, *, verb: str | None = None
) -> tuple[Any, ...]:
    """Batch caller-scoped PDP decisions in registry order."""

    from graph_os.api.policy import PolicyUnavailable, op_resource
    from graph_os.api.registry import Surface

    candidates = tuple(
        item
        for item in registry
        if Surface.MCP in item.surfaces and (verb is None or item.verb.value == verb)
    )
    try:
        decisions = await policy_gate.visible(
            [op_resource(item) for item in candidates], caller
        )
    except Exception as exc:
        raise PolicyUnavailable("policy decision point unavailable") from exc
    if len(decisions) != len(candidates):
        raise PolicyUnavailable("policy response alignment failed")
    return tuple(
        item
        for item, allowed in zip(candidates, decisions, strict=True)
        if allowed is True
    )


def _schema(model: Any) -> dict[str, Any]:
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model.model_json_schema()
    return {"$ref": model.path} if hasattr(model, "path") else {}


def describe_op(op: Any) -> dict[str, Any]:
    """Expose a complete typed descriptor from a visible registry entry."""

    return {
        "kind": "op",
        "op": op.id,
        "verb": op.verb.value,
        "summary": op.summary,
        "params_schema": _schema(op.params),
        "result_schema": _schema(op.result),
        "scopes": sorted(op.scopes),
        "authorized": True,
        "effect": op.effect.value,
        "confirm": op.confirm.value,
        "stability": op.stability.value,
        "examples": list(op.examples),
    }


def _fleet_descriptor(item: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve a fleet result while identifying its dispatch route."""

    return {
        **item,
        "kind": "fleet",
        "verb": "act",
        "op": "fleet.call",
        "authorized": True,
    }


async def find_visible(
    *,
    registry: Any,
    caller: Any,
    policy_gate: Any,
    resolver: Any,
    fleet_search: FleetSearch | None,
    intent: str | None = None,
    op: str | None = None,
    params: Mapping[str, Any] | None = None,
    scope_ref: str | None = None,
) -> dict[str, Any]:
    """Search only the caller's authorized operations and fleet items.

    ``fleet_search`` must apply its own scope, principal and Eunomia filter;
    unchecked child catalog rows are never accepted by this API.
    """

    visible = await visible_ops(registry, caller, policy_gate)
    selected = tuple(item for item in visible if op is None or item.id == op)
    domain = (params or {}).get("domain")
    if domain is not None:
        selected = tuple(item for item in selected if item.id.startswith(f"{domain}."))
    entries = [describe_op(item) for item in selected]
    if op is None and fleet_search is not None:
        fleet = await fleet_search(
            caller=caller,
            query=intent or "",
            context_budget_tokens=(params or {}).get("context_budget_tokens"),
        )
        entries.extend(_fleet_descriptor(item) for item in fleet)
    if intent and op is None:
        routing = [
            {**item, "op": item["id"]}
            if item["kind"] == "fleet" and item.get("id")
            else item
            for item in entries
        ]
        ranked = resolver.rank("find", intent, routing, scope_ref=scope_ref, top_k=20)
        by_id = {str(item.get("id") or item["op"]): item for item in entries}
        entries = [
            by_id[item.descriptor.id] for item in ranked if item.descriptor.id in by_id
        ]
    limit = (params or {}).get("limit", 20)
    limit = max(1, min(limit, 100)) if isinstance(limit, int) else 20
    return {
        "items": entries[:limit],
        "registry_digest": registry.digest,
        "next_cursor": None,
    }
