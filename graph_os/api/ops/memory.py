"""Caller-scoped durable memory and summary operations."""

from graph_os.api.registry import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


def _memory(name: str, method: str, *, write: bool = False) -> OpSpec:
    return OpSpec(
        id=f"memory.{name}",
        verb=Verb.WRITE if write else Verb.ASK,
        summary=f"{'Update' if write else 'Read'} graph memory {name.replace('_', ' ')}.",
        examples=(f"{'Update' if write else 'Show'} memory {name.replace('_', ' ')}",),
        params=EgSchemaRef(
            path=f"contract/schemas/method.request.json#/methods/{method}"
        ),
        result=EgSchemaRef(
            path=f"contract/schemas/result.graph.json#/methods/{method}"
        ),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({"memory:write" if write else "memory:read"}),
        effect=Effect.WRITE if write else Effect.READ,
        idempotency=Idempotency.KEY_REQUIRED if write else Idempotency.NATURAL,
        audit=AuditClass.EVENT if write else AuditClass.NONE,
    )


def specs() -> tuple[OpSpec, ...]:
    return (
        _memory("summary.children", "SummaryChildren"),
        _memory("summary.create", "CreateSummaryNode", write=True),
    )
