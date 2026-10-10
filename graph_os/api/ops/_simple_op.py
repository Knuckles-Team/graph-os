"""Shared ``OpSpec`` builder for an ops module with a handful of operations
bound by name to a local async handler.

Several ``graph_os.api.ops`` modules (for example :mod:`graph_os.api.ops.capacity`
and :mod:`graph_os.api.ops.identity`) each declared the same field-for-field
``OpSpec(id=..., verb=..., summary=..., examples=..., params=..., result=...,
binding=Composite(handler=...), scopes=..., effect=..., audit=..., surfaces=...)``
construction inline. That is the one real shape every such module needs, so it
lives here once instead of being reimplemented per module.
"""

from __future__ import annotations

from pydantic import BaseModel

from graph_os.api.registry import AuditClass, Composite, Effect, OpSpec, Surface, Verb


def simple_op(
    name: str,
    verb: Verb,
    summary: str,
    examples: tuple[str, ...],
    params: type[BaseModel],
    result: type[BaseModel],
    *,
    handler: str,
    scopes: frozenset[str],
    effect: Effect = Effect.READ,
    audit: AuditClass = AuditClass.NONE,
    surfaces: frozenset[Surface] = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A}),
) -> OpSpec:
    """Build one ``OpSpec`` bound to a fully-qualified ``module.function`` handler."""
    return OpSpec(
        id=name,
        verb=verb,
        summary=summary,
        examples=examples,
        params=params,
        result=result,
        binding=Composite(handler=handler),
        scopes=scopes,
        effect=effect,
        audit=audit,
        surfaces=surfaces,
    )


__all__ = ["simple_op"]
