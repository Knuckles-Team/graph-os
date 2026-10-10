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

from typing import NamedTuple

from pydantic import BaseModel

from graph_os.api.registry import AuditClass, Composite, Effect, OpSpec, Surface, Verb

_DEFAULT_SURFACES = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A})


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
    surfaces: frozenset[Surface] = _DEFAULT_SURFACES,
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


class OpSpecArgs(NamedTuple):
    """One row of an ops module's operation table; see :func:`build_operations`."""

    name: str
    verb: Verb
    summary: str
    examples: tuple[str, ...]
    params: type[BaseModel]
    result: type[BaseModel]
    handler: str
    scopes: frozenset[str]
    effect: Effect = Effect.READ
    audit: AuditClass = AuditClass.NONE
    surfaces: frozenset[Surface] = _DEFAULT_SURFACES


def build_operations(*rows: OpSpecArgs) -> tuple[OpSpec, ...]:
    """Build a module's ``operations()`` tuple from a table of :class:`OpSpecArgs` rows.

    Every curated ``graph_os.api.ops`` module exports an ``operations() ->
    tuple[OpSpec, ...]`` entry point for ``registry_factory.get_registry()``;
    that wrapper is identical shape across domains and cannot itself be
    collapsed (one module per domain, per AGENTS.md). This builder is what a
    module's ``operations()`` delegates to, so only the per-operation *data*
    -- not the ``simple_op`` call shape -- is written out per module.
    """
    return tuple(
        simple_op(
            row.name,
            row.verb,
            row.summary,
            row.examples,
            row.params,
            row.result,
            handler=row.handler,
            scopes=row.scopes,
            effect=row.effect,
            audit=row.audit,
            surfaces=row.surfaces,
        )
        for row in rows
    )


__all__ = ["OpSpecArgs", "build_operations", "simple_op"]
