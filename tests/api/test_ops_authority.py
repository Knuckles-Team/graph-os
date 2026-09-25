"""Exact EG authority for curated operator operations."""

from graph_os.api.ops import analytics, ops, usage
from graph_os.api.registry import EgMethod, Executor, PrincipalRule


def test_curated_operator_methods_use_exact_eg_scope() -> None:
    expected = {
        "Health": "service:control",
        "ListGraphs": "graph:read",
        "RebalancePlan": "admin:cluster-read",
        "Backup": "admin:backup",
        "Restore": "admin:backup",
        "CreateGraph": "graph:admin",
        "DeleteGraph": "graph:admin",
        "RebalanceExecute": "admin:cluster",
        "Reshard": "admin:cluster",
    }
    for op in ops.specs():
        method = op.binding.service
        assert op.scopes == frozenset({expected[method]})
        assert op.executor is Executor.CALLER
        if expected[method] == "admin:cluster-read":
            assert op.principals is PrincipalRule.SERVICE_ONLY
        if expected[method] == "service:control":
            assert op.principals is PrincipalRule.ANY


def test_timeseries_mutations_are_service_only_under_caller_authority() -> None:
    items = {op.id: op for op in analytics.specs()}
    for name in ("analytics.series.define", "analytics.series.drop"):
        op = items[name]
        assert op.scopes == frozenset({"timeseries:write"})
        assert op.principals is PrincipalRule.SERVICE_ONLY
        assert op.executor is Executor.CALLER
    assert items["analytics.series.list"].principals is PrincipalRule.ANY


def test_resource_stats_page_has_one_direct_owner() -> None:
    bound = [
        op.id
        for module in (ops, usage)
        for op in module.specs()
        if isinstance(op.binding, EgMethod)
        and op.binding.service == "ResourceStatsPage"
    ]
    assert bound == ["usage.resources"]
