"""Authority and EG-method contracts for curated query domain operations."""

import pytest

pytest.importorskip("graph_os.api.registry")

from graph_os.api.ops import analytics, ontology, query, search  # noqa: E402
from graph_os.api.registry import Effect, EgMethod, Verb  # noqa: E402


def test_curated_ops_bind_unique_eg_methods_with_exact_scopes() -> None:
    expected = {
        "query.uql": ("Uql", "query:unified"),
        "query.sparql": ("Sparql", "sparql:read"),
        "query.sql": ("Sql", "query:sql"),
        "search.semantic": ("SemanticSearch", "compute:semantic"),
        "indexes.semantic.manage": ("SemanticIndex", "semantic:binding-write"),
        "ontology.classes": ("GraphSchemaClasses", "owl:read"),
        "ontology.validate": ("ShaclValidate", "validation:read"),
        "analytics.series.define": ("TsDefineSeries", "timeseries:write"),
        "analytics.series.drop": ("TsDeleteSeries", "timeseries:write"),
        "analytics.series.list": ("TsListSeries", "timeseries:read"),
    }
    ops = query.specs() + search.specs() + ontology.specs() + analytics.specs()
    assert len(ops) == len(expected)
    assert set(expected) == {op.id for op in ops}
    assert len({op.binding.service for op in ops}) == len(ops)
    for op in ops:
        method, scope = expected[op.id]
        assert isinstance(op.binding, EgMethod)
        assert op.binding.service == op.binding.op == method
        assert op.scopes == frozenset({scope})


def test_sql_and_index_mutations_do_not_enter_read_only_verbs() -> None:
    ops = {op.id: op for op in query.specs() + search.specs() + analytics.specs()}
    assert ops["query.sql"].verb is Verb.WRITE
    assert ops["query.sql"].effect is Effect.WRITE
    assert ops["indexes.semantic.manage"].verb is Verb.MANAGE
    assert ops["indexes.semantic.manage"].effect is Effect.WRITE
    assert ops["analytics.series.drop"].effect is Effect.DESTRUCTIVE
