"""Receipt reads fix the EG variant and bind the verified caller tenant."""

from types import SimpleNamespace

import pytest

pytest.importorskip("graph_os.api.registry")

from graph_os.api.ops import decide  # noqa: E402
from graph_os.api.registry import Composite  # noqa: E402


DIGEST = "sha256:" + "a" * 64


def test_receipt_ops_are_admin_scoped_reads() -> None:
    ops = {op.id: op for op in decide.specs()}
    for name in ("decide.eval.receipt", "decide.eval.receipts"):
        op = ops[name]
        assert op.scopes == frozenset({"admin:decision-eval"})
        assert isinstance(op.binding, Composite)


@pytest.mark.asyncio
async def test_handlers_fix_variant_and_tenant(monkeypatch) -> None:
    context = SimpleNamespace(caller=SimpleNamespace(tenant="verified"))
    calls = []

    async def capture(_context, variant, request):
        calls.append((variant, request))
        return {"ok": True}

    monkeypatch.setattr(decide, "_receipt_read", capture)
    await decide.receipt_handler(context, {"receipt_digest": DIGEST}, None)
    await decide.receipts_handler(context, {"after": DIGEST, "limit": 2}, None)
    assert calls == [
        ("receipt", {"receipt_digest": DIGEST}),
        ("receipts", {"after": DIGEST, "limit": 2}),
    ]
    with pytest.raises(ValueError):
        await decide.receipt_handler(
            context, {"receipt_digest": DIGEST, "tenant_id": "other"}, None
        )
    with pytest.raises(ValueError):
        await decide.receipts_handler(context, {"limit": 51}, None)


@pytest.mark.asyncio
async def test_transport_receives_only_fixed_read_and_verified_tenant(monkeypatch) -> None:
    from epistemic_graph.generated import coordination

    transport = object()
    context = SimpleNamespace(
        caller=SimpleNamespace(tenant="verified"),
        client=SimpleNamespace(_client=transport),
    )
    observed = {}

    async def send(client, params):
        observed.update(client=client, params=params)
        return SimpleNamespace(payload={"receipts": []})

    monkeypatch.setattr(coordination, "send_decision_eval", send)
    result = await decide.receipts_handler(context, {"limit": 1}, None)
    assert result == {"receipts": []}
    assert observed == {
        "client": transport,
        "params": {
            "op": {
                "op": "receipts",
                "request": {"tenant_id": "verified", "after": None, "limit": 1},
            }
        },
    }
