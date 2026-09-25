"""Operator control operations; disruptive requests require console confirmation."""

from graph_os.api.registry import (
    AuditClass,
    Confirm,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Verb,
)


def _engine(
    name: str, method: str, result: str, engine_scope: str, *, destructive: bool = False
) -> OpSpec:
    return OpSpec(
        id=f"ops.{name}",
        verb=Verb.MANAGE if destructive else Verb.ASK,
        summary=f"{'Execute' if destructive else 'Inspect'} {name.replace('_', ' ')}.",
        examples=(f"{'Execute' if destructive else 'Show'} {name.replace('_', ' ')}",),
        params=EgSchemaRef(
            path=f"contract/schemas/method.request.json#/methods/{method}"
        ),
        result=EgSchemaRef(
            path=f"contract/schemas/result.{result}.json#/methods/{method}"
        ),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({engine_scope}),
        effect=Effect.DESTRUCTIVE if destructive else Effect.READ,
        principals=(
            PrincipalRule.SERVICE_ONLY
            if engine_scope == "admin:cluster-read"
            else PrincipalRule.HUMAN_UNDELEGATED
            if destructive
            else PrincipalRule.ANY
        ),
        confirm=Confirm.CONSOLE if destructive else None,
        idempotency=Idempotency.KEY_REQUIRED if destructive else Idempotency.NATURAL,
        audit=AuditClass.EVENT if destructive else AuditClass.NONE,
    )


def specs() -> tuple[OpSpec, ...]:
    return (
        _engine("health", "Health", "cluster", "service:control"),
        _engine("resources", "ResourceStatsPage", "coordination", "service:control"),
        _engine("graphs.list", "ListGraphs", "cluster", "graph:read"),
        _engine("shards.plan", "RebalancePlan", "cluster", "admin:cluster-read"),
        _engine("backup", "Backup", "storage", "admin:backup", destructive=True),
        _engine("restore", "Restore", "storage", "admin:backup", destructive=True),
        _engine(
            "graphs.create", "CreateGraph", "cluster", "graph:admin", destructive=True
        ),
        _engine(
            "graphs.delete", "DeleteGraph", "cluster", "graph:admin", destructive=True
        ),
        _engine(
            "shards.execute",
            "RebalanceExecute",
            "cluster",
            "admin:cluster",
            destructive=True,
        ),
        _engine(
            "shards.reshard", "Reshard", "cluster", "admin:cluster", destructive=True
        ),
    )
