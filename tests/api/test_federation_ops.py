"""Federation intent declarations and their required EG wire methods."""

from graph_os.api.ops.federation import specs
from graph_os.api.registry import (
    Confirm,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    PrincipalRule,
)


def test_federation_ops_bind_six_distinct_eg_methods() -> None:
    ops = specs()
    assert {op.id for op in ops} == {
        f"federation.sources.{name}"
        for name in ("register", "list", "get", "share", "unshare", "probe")
    }
    assert (
        len({op.binding.service for op in ops if isinstance(op.binding, EgMethod)}) == 6
    )
    for op in ops:
        assert isinstance(op.binding, EgMethod)
        assert isinstance(op.params, EgSchemaRef)
        assert isinstance(op.result, EgSchemaRef)
        assert op.binding.service == op.binding.op
        assert op.scopes == frozenset({"federation:admin"})
        assert op.params.path.endswith(f"/methods/{op.binding.op}")
        assert op.result.path.endswith(f"/methods/{op.binding.op}")


def test_sharing_requires_human_console_confirmation() -> None:
    ops = {op.id: op for op in specs()}
    for name in ("share", "unshare"):
        op = ops[f"federation.sources.{name}"]
        assert op.effect is Effect.ADMIN
        assert op.principals is PrincipalRule.HUMAN_UNDELEGATED
        assert op.confirm is Confirm.CONSOLE
        assert op.idempotency is Idempotency.KEY_REQUIRED
    assert ops["federation.sources.probe"].effect is Effect.READ
    assert ops["federation.sources.register"].effect is Effect.WRITE
