"""MCPI-20 authority and engine contract checks."""

from types import SimpleNamespace

import pytest

from graph_os.api.ops import graph, memory, ops, security, telemetry, usage
from graph_os.api.registry import (
    Composite,
    Confirm,
    Effect,
    EgMethod,
    Executor,
    PrincipalRule,
    Verb,
)

MODULES = (telemetry, security, ops, usage, memory, graph)


def _specs():
    return {op.id: op for module in MODULES for op in module.specs()}


def test_domain_ops_bind_named_engine_contract_methods() -> None:
    all_ops = _specs()
    assert len(all_ops) == sum(len(module.specs()) for module in MODULES)
    for op in all_ops.values():
        if isinstance(op.binding, Composite):
            continue
        assert isinstance(op.binding, EgMethod)
        assert op.binding.service == op.binding.op
        for schema in (op.params, op.result):
            path, method = schema.path.split("#/methods/")
            assert path.startswith("contract/schemas/")
            assert method == op.binding.service


def test_operator_mutations_need_console_human_and_exact_eg_scope() -> None:
    all_ops = _specs()
    for op_id, scope in {
        "ops.backup": "admin:backup",
        "ops.restore": "admin:backup",
        "ops.graphs.create": "graph:admin",
        "ops.graphs.delete": "graph:admin",
        "ops.shards.execute": "admin:cluster",
        "ops.shards.reshard": "admin:cluster",
    }.items():
        op = all_ops[op_id]
        assert op.verb is Verb.MANAGE
        assert op.effect is Effect.DESTRUCTIVE
        assert op.principals is PrincipalRule.HUMAN_UNDELEGATED
        assert op.confirm is Confirm.CONSOLE
        assert op.scopes == frozenset({scope})


def test_graph_reads_and_usage_bind_engine_without_local_store() -> None:
    all_ops = _specs()
    assert all_ops["graph.nodes.list"].binding.service == "GetNodesByLabel"
    assert all_ops["graph.edges.list"].binding.service == "GetEdgesPage"
    assert all_ops["usage.resources"].binding.service == "ResourceStatsPage"
    assert all_ops["usage.resources"].scopes == frozenset({"service:control"})
    assert all_ops["usage.resources"].executor is Executor.CALLER
    assert all_ops["usage.resources"].principals is PrincipalRule.ANY
    assert all_ops["security.audit.verify"].binding.service == "AuditVerify"
    assert all_ops["telemetry.cep.poll"].binding.service == "CepPoll"
    derive = all_ops["telemetry.derive.run"]
    assert derive.binding.service == "TelemetryDerive"
    assert derive.scopes == frozenset({"telemetry:derive"})
    assert derive.verb is Verb.ACT
    assert derive.effect is Effect.WRITE


@pytest.mark.asyncio
async def test_telemetry_fact_pages_fix_label_and_bound_limit() -> None:
    calls = []

    class Nodes:
        async def list_by_label(self, label, limit, *, after):
            calls.append((label, limit, after))
            return [("fact:1", {"type": label})]

    context = SimpleNamespace(client=SimpleNamespace(nodes=Nodes()))
    all_ops = _specs()
    result = await telemetry.facts_page_handler(
        context, {"limit": 1, "cursor": "fact:0"}, all_ops["telemetry.anomalies"]
    )
    assert calls == [("HealthAnomaly", 1, "fact:0")]
    assert result == {
        "items": [{"id": "fact:1", "properties": {"type": "HealthAnomaly"}}],
        "next_cursor": "fact:1",
    }
    await telemetry.facts_page_handler(context, {}, all_ops["telemetry.conformance"])
    assert calls[-1] == ("ConformanceViolation", 50, None)
    with pytest.raises(ValueError):
        await telemetry.facts_page_handler(
            context, {"limit": 0}, all_ops["telemetry.anomalies"]
        )
    assert len(calls) == 2
