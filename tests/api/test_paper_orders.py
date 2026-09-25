"""Paper orders reserve durable authority before contacting a fake connector."""

import json
import sys
from types import SimpleNamespace

import pytest

from graph_os.api.ops.finance import specs
from graph_os.finance.paper_orders import PAPER_KIND, submit


class Leases:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    async def issue(self, **request: object) -> dict[str, str]:
        key = str(request["lease_id"])
        if key in self.rows:
            return {"outcome": "collision"}
        self.rows[key] = {**request, "status": "active"}
        return {"outcome": "issued"}

    async def get(self, *, tenant: str, lease_id: str) -> dict[str, object] | None:
        return self.rows.get(lease_id)


def context(
    leases: Leases, *, key: str = "request-1", scope: bool = True
) -> SimpleNamespace:
    caller = SimpleNamespace(
        tenant="tenant-a",
        principal="human-a",
        effective_scopes=frozenset({"finance:paper-trade"} if scope else set()),
    )
    return SimpleNamespace(
        caller=caller,
        client=SimpleNamespace(control_leases=leases),
        service_identity=True,
        idempotency_key=key,
    )


def test_paper_op_declares_exact_domain_and_service_authority() -> None:
    op = next(item for item in specs() if item.id == "finance.paper.submit")
    assert op.scopes == frozenset({"finance:paper-trade"})
    assert op.executor_scopes == frozenset(
        {"broker:write", "lease:read", "lease:write"}
    )
    assert op.subject.source.value == "caller_tenant"
    assert op.effect.value == "write"
    assert op.idempotency.value == "key_required"


@pytest.mark.asyncio
async def test_retry_and_changed_intent_never_dispatch_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leases = Leases()
    calls: list[dict[str, object]] = []

    class Mux:
        async def delegate_server_tool(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return json.dumps({"status": "filled", "order_id": "PAPER-1"})

    async def served(operation: object) -> object:
        return await operation(Mux())

    monkeypatch.setitem(
        sys.modules,
        "graph_os.fleet.shared_multiplexer",
        SimpleNamespace(run_on_served_multiplexer=served),
    )
    request = {"intent": {"symbol": "SOL", "side": "buy", "qty": 2}}
    first = await submit(context(leases), request)
    assert first["status"] == "filled"
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "emerald_paper_orders"
    assert calls[0]["arguments"]["limit_price"] == 0.0
    assert next(iter(leases.rows.values()))["kind"] == PAPER_KIND

    retry = await submit(context(leases), request)
    assert retry["status"] == "indeterminate"
    assert len(calls) == 1
    with pytest.raises(ValueError, match="conflicts"):
        await submit(
            context(leases), {"intent": {"symbol": "SOL", "side": "buy", "qty": 3}}
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_connector_outcome_stays_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leases = Leases()
    count = 0

    async def served(operation: object) -> object:
        nonlocal count
        count += 1
        raise TimeoutError("simulated timeout")

    monkeypatch.setitem(
        sys.modules,
        "graph_os.fleet.shared_multiplexer",
        SimpleNamespace(run_on_served_multiplexer=served),
    )
    request = {"intent": {"symbol": "SOL", "side": "buy", "qty": 2}}
    assert (await submit(context(leases), request))["status"] == "indeterminate"
    assert (await submit(context(leases), request))["status"] == "indeterminate"
    assert count == 1


@pytest.mark.asyncio
async def test_missing_scope_reserves_nothing() -> None:
    leases = Leases()
    with pytest.raises(PermissionError):
        await submit(
            context(leases, scope=False),
            {"intent": {"symbol": "SOL", "side": "buy", "qty": 2}},
        )
    assert leases.rows == {}
