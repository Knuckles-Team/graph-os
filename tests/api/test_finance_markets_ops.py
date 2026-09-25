"""Finance/Markets op declarations and server-side authority path."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from graph_os.api.ops import finance, markets
from graph_os.api.registry import Executor, Surface
from graph_os.finance.markets import MarketsService


def _context(**changes: object) -> SimpleNamespace:
    caller = SimpleNamespace(
        tenant="tenant-a",
        principal="human-a",
        effective_scopes=frozenset({"finance:read", "finance:alerts"}),
        engine_claims={"tenant": "tenant-a", "principal": "human-a", "agent_id": "a"},
        delegated=False,
    )
    values = {
        "caller": caller,
        "client": object(),
        "service_identity": True,
        "idempotency_key": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_service_ops_are_caller_tenant_bound_and_schemas_reject_subject_spoof() -> None:
    for op in (*finance.specs(), *markets.specs()):
        if op.executor is Executor.SERVICE:
            assert op.subject.path == "$caller.tenant"
            assert op.scopes
            assert op.executor_scopes
            with pytest.raises(ValidationError):
                op.params.model_validate({"subject": "other-tenant"})


def test_live_order_decisions_are_console_only_and_two_phase() -> None:
    for op in finance.specs():
        if op.id in {"finance.orders.approve", "finance.orders.deny"}:
            assert op.surfaces == frozenset({Surface.CONSOLE})
            assert op.confirm.value == "console"
            assert op.principals.value == "human_undelegated"


@pytest.mark.asyncio
async def test_finance_handler_uses_executor_client_and_verified_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.finance import finance_ops as legacy

    observed = {}

    async def handler(call: object) -> dict[str, str]:
        observed["client"] = call.client
        observed["owner"] = call.owner
        observed["action"] = call.request.action
        return {"ok": "yes"}

    monkeypatch.setitem(legacy._HANDLERS, "alerts", handler)
    context = _context()
    op = next(item for item in finance.specs() if item.id == "finance.alerts.inbox")
    assert await finance.execute(context, {}, op) == {"ok": "yes"}
    assert observed == {
        "client": context.client,
        "owner": legacy.principal_ref("human-a"),
        "action": "alerts",
    }
    with pytest.raises(PermissionError):
        await finance.execute(_context(service_identity=False), {}, op)


@pytest.mark.asyncio
async def test_markets_listings_run_on_server_service_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = {}

    async def catalog(self: MarketsService) -> list[object]:
        observed["tenant"] = self.tenant
        observed["client"] = self.gateway._client()
        return []

    monkeypatch.setattr(MarketsService, "_catalog", catalog)
    context = _context()
    op = next(item for item in markets.specs() if item.id == "markets.listings.list")
    assert await markets.execute(context, {}, op) == {"listings": [], "total": 0}
    assert observed == {"tenant": "tenant-a", "client": context.client}
    with pytest.raises(PermissionError):
        await markets.execute(_context(service_identity=False), {}, op)


@pytest.mark.asyncio
async def test_markets_catalog_reads_service_engine_and_preserves_series_timeframe() -> (
    None
):
    calls: list[tuple[str, int]] = []

    async def by_label(label: str, limit: int) -> list[tuple[str, dict[str, object]]]:
        calls.append((label, limit))
        if label == "Listing":
            return [
                (
                    "listing:SOL",
                    {
                        "listedInstrument": "instrument:SOL",
                        "quoteInstrument": "instrument:USD",
                        "listedOn": "venue:one",
                        "listingType": "spot",
                    },
                )
            ]
        return [
            (
                "series:SOL:1D",
                {
                    "barSeriesOf": "listing:SOL",
                    "barTimeframe": "1D",
                    "tsdbSeriesId": "finance.bars.sol",
                    "tickSize": "0.01",
                    "volumeStep": "1",
                },
            )
        ]

    async def batch(ids: list[str]) -> dict[str, dict[str, str]]:
        return {
            "instrument:SOL": {
                "symbol": "SOL",
                "name": "Solana",
                "assetClass": "crypto",
            },
            "instrument:USD": {"symbol": "USD"},
            "venue:one": {"name": "Venue One"},
        }

    client = SimpleNamespace(
        nodes=SimpleNamespace(list_by_label=by_label, properties_batch=batch)
    )
    service = MarketsService(client, "tenant-a", "human-a")
    result = await service.listings("SOL", "crypto", 10)
    assert result["total"] == 1
    assert result["listings"][0]["timeframes"] == ["1D"]
    assert calls == [("Listing", 5000), ("BarSeries", 20000)]


@pytest.mark.asyncio
async def test_positions_reads_admitted_fleet_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "mode": "paper",
        "venue": {"positions": [{"symbol": "SOL"}]},
        "paper": {"positions": [{"symbol": "BTC"}]},
    }

    class Mux:
        async def delegate_server_tool(self, **kwargs: object) -> str:
            assert kwargs["server_name"] == "emerald-exchange-mcp"
            assert kwargs["tool_name"] == "emerald_positions_snapshot"
            return json.dumps(snapshot)

    async def on_served(operation: object) -> object:
        return await operation(Mux())

    monkeypatch.setitem(
        sys.modules,
        "graph_os.fleet.shared_multiplexer",
        SimpleNamespace(run_on_served_multiplexer=on_served),
    )
    venue = next(op for op in finance.specs() if op.id == "finance.positions.list")
    paper = next(
        op for op in finance.specs() if op.id == "finance.paper.positions.list"
    )
    assert (await finance.positions(_context(), {}, venue))["account"] == snapshot[
        "venue"
    ]
    assert (await finance.positions(_context(), {}, paper))["account"] == snapshot[
        "paper"
    ]
    with pytest.raises(PermissionError):
        await finance.positions(_context(service_identity=False), {}, venue)


class LeaseStore:
    def __init__(self) -> None:
        self.leases: dict[str, dict[str, object]] = {}

    async def issue(self, **request: object) -> dict[str, str]:
        lease_id = str(request["lease_id"])
        if lease_id in self.leases:
            return {"outcome": "exists"}
        self.leases[lease_id] = {**request, "status": "active"}
        return {"outcome": "issued"}

    async def get(self, *, tenant: str, lease_id: str) -> dict[str, object] | None:
        return self.leases.get(lease_id)


@pytest.mark.asyncio
async def test_order_proposal_retry_and_key_conflict() -> None:
    from graph_os.finance.models import OrderIntent
    from graph_os.finance.orders import OrderRefused, propose_order

    leases = LeaseStore()
    client = SimpleNamespace(control_leases=leases)
    claims = {"tenant": "tenant-a", "principal": "human-a"}
    intent = OrderIntent(symbol="SOL", side="buy", qty=2)
    first = await propose_order(client, claims, intent, "first", 1000, "key-1")
    retry = await propose_order(client, claims, intent, "first", 2000, "key-1")
    assert retry == first
    assert len(leases.leases) == 1
    with pytest.raises(OrderRefused, match="ORDER_IDEMPOTENCY_CONFLICT"):
        await propose_order(
            client,
            claims,
            OrderIntent(symbol="SOL", side="buy", qty=3),
            "second",
            2000,
            "key-1",
        )
    other = await propose_order(
        client,
        {"tenant": "tenant-a", "principal": "human-b"},
        intent,
        "other",
        2000,
        "key-1",
    )
    assert other["approval_id"] != first["approval_id"]


@pytest.mark.asyncio
async def test_share_retry_reuses_exact_lease() -> None:
    from graph_os.finance.markets_gateway import MarketsUnavailable
    from graph_os.finance.markets_snapshots import Caller, create_share

    leases = LeaseStore()

    class Gateway:
        async def market(self, op: str, **kwargs: object) -> dict[str, object]:
            encoded = json.dumps(kwargs["draft"], sort_keys=True)
            return {
                "digest": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
                "draft": kwargs["draft"],
            }

        async def create_node(self, *args: object) -> bool:
            return True

        async def issue_lease(self, **kwargs: object) -> dict[str, str]:
            return await leases.issue(**kwargs)

        async def get_lease(self, **kwargs: str) -> dict[str, object] | None:
            return await leases.get(**kwargs)

    caller = Caller("tenant-a", "sha256:human-a")
    draft = {"key": {"series": {"listing_id": "listing:one"}}}
    first = await create_share(Gateway(), caller, draft, 1000, 24, "key-1")
    retry = await create_share(Gateway(), caller, draft, 2000, 24, "key-1")
    assert retry == first
    assert len(leases.leases) == 1
    with pytest.raises(MarketsUnavailable, match="could not be issued"):
        await create_share(
            Gateway(),
            caller,
            {"key": {"series": {"listing_id": "listing:other"}}},
            2000,
            24,
            "key-1",
        )
