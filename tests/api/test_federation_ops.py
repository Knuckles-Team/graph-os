"""Federation intents must match the EG methods callable in Train 5."""

from graph_os.api.ops.federation import specs
from graph_os.api.registry import Effect, EgMethod, EgSchemaRef, Idempotency


def test_only_served_foreign_source_registration_is_advertised() -> None:
    (op,) = specs()
    assert op.id == "federation.sources.register"
    assert op.binding == EgMethod(
        service="RegisterForeignSource", op="RegisterForeignSource"
    )
    assert op.params == EgSchemaRef(
        path="contract/schemas/method.request.json#/methods/RegisterForeignSource"
    )
    assert op.result == EgSchemaRef(
        path="contract/schemas/result.cluster.json#/methods/RegisterForeignSource"
    )
    assert op.scopes == frozenset({"federation:admin"})
    assert op.effect is Effect.WRITE
    assert op.idempotency is Idempotency.KEY_REQUIRED


def test_unserved_foreign_source_methods_are_not_exposed() -> None:
    ids = {op.id for op in specs()}
    assert ids.isdisjoint(
        {
            "federation.sources.list",
            "federation.sources.get",
            "federation.sources.share",
            "federation.sources.unshare",
            "federation.sources.probe",
        }
    )
