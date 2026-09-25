"""MCPI-20 authority and engine contract checks."""

from graph_os.api.ops import graph, memory, ops, security, telemetry, usage
from graph_os.api.registry import Confirm, Effect, EgMethod, PrincipalRule, Verb

MODULES = (telemetry, security, ops, usage, memory, graph)


def _specs():
    return {op.id: op for module in MODULES for op in module.specs()}


def test_domain_ops_bind_named_engine_contract_methods() -> None:
    all_ops = _specs()
    assert len(all_ops) == sum(len(module.specs()) for module in MODULES)
    for op in all_ops.values():
        assert isinstance(op.binding, EgMethod)
        assert op.binding.service == op.binding.op
        for schema in (op.params, op.result):
            path, method = schema.path.split("#/methods/")
            assert path.startswith("contract/schemas/")
            assert method == op.binding.service


def test_operator_mutations_need_console_human_and_two_scopes() -> None:
    all_ops = _specs()
    for op_id in (
        "ops.backup",
        "ops.restore",
        "ops.graphs.create",
        "ops.graphs.delete",
        "ops.shards.execute",
        "ops.shards.reshard",
    ):
        op = all_ops[op_id]
        assert op.verb is Verb.MANAGE
        assert op.effect is Effect.DESTRUCTIVE
        assert op.principals is PrincipalRule.HUMAN_UNDELEGATED
        assert op.confirm is Confirm.CONSOLE
        assert "ops:admin" in op.scopes
        assert len(op.scopes) == 2


def test_graph_reads_and_usage_bind_engine_without_local_store() -> None:
    all_ops = _specs()
    assert all_ops["graph.nodes.list"].binding.service == "GetNodesByLabel"
    assert all_ops["graph.edges.list"].binding.service == "GetEdgesPage"
    assert all_ops["usage.resources"].binding.service == "ResourceStatsPage"
    assert all_ops["usage.resources"].scopes == frozenset(
        {"ops:read", "service:control"}
    )
    assert all_ops["security.audit.verify"].binding.service == "AuditVerify"
    assert all_ops["telemetry.cep.poll"].binding.service == "CepPoll"
