"""Raw graph reads and caller-authorized mutations."""

from graph_os.api.registry import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


def _graph(name: str, method: str, scope: str, *, write: bool = False) -> OpSpec:
    return OpSpec(
        id=f"graph.{name}",
        verb=Verb.WRITE if write else Verb.ASK,
        summary=f"{'Mutate' if write else 'Read'} graph {name.replace('.', ' ')}.",
        examples=(f"{'Update' if write else 'Show'} graph {name.replace('.', ' ')}",),
        params=EgSchemaRef(
            path=f"contract/schemas/method.request.json#/methods/{method}"
        ),
        result=EgSchemaRef(
            path=f"contract/schemas/result.graph.json#/methods/{method}"
        ),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({scope}),
        effect=Effect.WRITE if write else Effect.READ,
        idempotency=Idempotency.KEY_REQUIRED if write else Idempotency.NATURAL,
        audit=AuditClass.EVENT if write else AuditClass.NONE,
    )


def specs() -> tuple[OpSpec, ...]:
    return (
        _graph("nodes.list", "GetNodesByLabel", "node:read"),
        _graph("nodes.get", "GetNodeProperties", "node:read"),
        _graph("nodes.add", "AddNode", "node:write", write=True),
        _graph("nodes.remove", "RemoveNode", "node:write", write=True),
        _graph("edges.list", "GetEdgesPage", "edge:read"),
        _graph("edges.get", "GetEdgeProperties", "edge:read"),
        _graph("edges.add", "AddEdge", "edge:write", write=True),
        _graph("edges.remove", "RemoveEdge", "edge:write", write=True),
    )
