"""Caller-filtered operation and fleet discovery for the MCP ``find`` verb."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
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


#: Synthetic ``ask`` descriptor (GRAPHOS-HOST-R023) offered to the resolver so a
#: free-text question with no matching registry operation can still resolve to
#: the natural-language fallback instead of an outright refusal. It never wins
#: a ranking on its own terms (empty examples/tags) -- only the resolver's own
#: last-resort branch selects it, when every real candidate scores too low.
NL_FALLBACK_OP_ID = "query.uql"


def nl_fallback_descriptor() -> dict[str, Any]:
    """The ask-verb descriptor naming the natural-language fallback target."""

    return {
        "kind": "op",
        "op": NL_FALLBACK_OP_ID,
        "verb": "ask",
        "summary": "Free-text knowledge graph question (natural-language fallback)",
        "params_schema": {},
        "examples": (),
        "domain": "",
        "tags": (),
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


def _matching_ops(
    visible: Iterable[Any], op: str | None, domain: Any
) -> tuple[Any, ...]:
    """Filter visible ops to an exact id and, if given, a dotted domain prefix."""

    selected = tuple(item for item in visible if op is None or item.id == op)
    if domain is None:
        return selected
    return tuple(item for item in selected if item.id.startswith(f"{domain}."))


async def _fleet_entries(
    fleet_search: FleetSearch,
    *,
    caller: Any,
    intent: str | None,
    params: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Run the caller-filtered fleet catalog search for unmatched discovery."""

    fleet = await fleet_search(
        caller=caller,
        query=intent or "",
        context_budget_tokens=(params or {}).get("context_budget_tokens"),
    )
    return [_fleet_descriptor(item) for item in fleet]


def _ranked_entries(
    entries: list[dict[str, Any]],
    *,
    resolver: Any,
    intent: str,
    scope_ref: str | None,
) -> list[dict[str, Any]]:
    """Reorder merged op and fleet entries by the resolver's own ranking."""

    routing = [
        {**item, "op": item["id"]}
        if item["kind"] == "fleet" and item.get("id")
        else item
        for item in entries
    ]
    ranked = resolver.rank("find", intent, routing, scope_ref=scope_ref, top_k=20)
    by_id = {str(item.get("id") or item["op"]): item for item in entries}
    return [by_id[item.descriptor.id] for item in ranked if item.descriptor.id in by_id]


def _bounded_limit(params: Mapping[str, Any] | None) -> int:
    limit = (params or {}).get("limit", 20)
    return max(1, min(limit, 100)) if isinstance(limit, int) else 20


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
    selected = _matching_ops(visible, op, (params or {}).get("domain"))
    entries = [describe_op(item) for item in selected]
    if op is None and fleet_search is not None:
        entries.extend(
            await _fleet_entries(
                fleet_search, caller=caller, intent=intent, params=params
            )
        )
    if intent and op is None:
        entries = _ranked_entries(
            entries, resolver=resolver, intent=intent, scope_ref=scope_ref
        )
    return {
        "items": entries[: _bounded_limit(params)],
        "registry_digest": registry.digest,
        "next_cursor": None,
    }
