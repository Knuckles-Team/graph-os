"""Server-side Markets application over the service-bound EG client.

The finance calculations and sealing remain in EG. This module keeps the
Markets host's response shapes while moving engine access out of WebUI.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from graph_os.finance.markets_catalog import Listing, load_catalog, search
from graph_os.finance.markets_chart import (
    LAYERS,
    ChartRequest,
    build_chart,
    macro_events,
)
from graph_os.finance.markets_gateway import EngineGateway, MarketsUnavailable
from graph_os.finance.markets_scanner import (
    MAX_UNIVERSE,
    ScanQuery,
    run_scan,
    scan_states,
    universe,
)
from graph_os.finance.markets_series import (
    SignalSpec,
    load_series,
    load_signal,
)
from graph_os.finance.markets_snapshots import (
    Caller,
    actor_ref,
    build_draft,
    create_share,
    read_share,
    revoke_share,
)
from graph_os.finance.markets_timeframes import TIMEFRAMES, range_start


async def _invoke(method: Any, *args: Any, deadline: float, **kwargs: Any) -> Any:
    return await asyncio.wait_for(method(*args, **kwargs), timeout=deadline)


class MarketsService:
    """One request-bound service client; caller authorization precedes construction."""

    def __init__(
        self,
        client: Any,
        tenant: str,
        principal: str,
        idempotency_key: str | None = None,
    ) -> None:
        self.gateway = EngineGateway(lambda: client, _invoke)
        self.tenant = tenant
        self.caller = Caller(tenant, actor_ref(principal))
        self.idempotency_key = idempotency_key

    async def _catalog(self) -> list[Listing]:
        return await load_catalog(self.gateway)

    async def _listing(self, listing_id: str) -> Listing:
        for listing in await self._catalog():
            if listing.listing_id == listing_id:
                return listing
        raise LookupError("unknown listing")

    async def listings(
        self, query: str, asset_class: str | None, limit: int
    ) -> dict[str, Any]:
        items = await self._catalog()
        return {
            "listings": [
                item.public() for item in search(items, query, asset_class, limit)
            ],
            "total": len(items),
        }

    async def chart(self, params: dict[str, Any]) -> dict[str, Any]:
        found = await self._listing(params["listing_id"])
        now = time.time_ns()
        timeframe = params["timeframe"]
        layers = frozenset(params["layers"])
        if not layers <= set(LAYERS):
            raise ValueError("unknown chart layer")
        spec = SignalSpec(
            params["atr_period"], round(params["multiplier"] * 1000), params["basis"]
        )
        request = ChartRequest(
            timeframe,
            range_start(params["range"], now),
            params["width"],
            layers,
            spec,
            now,
        )
        data = await load_series(self.gateway, found, timeframe, now)
        view, _ = await build_chart(self.gateway, data, request)
        return {"listing": found.public(), "range": params["range"], **view}

    async def scanner(self, params: dict[str, Any]) -> dict[str, Any]:
        query = ScanQuery(
            params["timeframe"],
            params["asset_class"],
            params["quote"],
            params["direction"],
            tuple(params["status"]),
            params["flipped_within_days"],
            params["near_ath"],
            params["limit"],
        )
        now = time.time_ns()
        members = universe(await self._catalog(), query)
        scanned = await scan_states(self.gateway, members[:MAX_UNIVERSE], query, now)
        page = await run_scan(self.gateway, scanned, query, now)
        return {
            "timeframe": query.timeframe,
            "universe": {
                "listings": len(members),
                "scanned": len(scanned),
                "truncated": len(members) > MAX_UNIVERSE,
            },
            **page,
        }

    async def share(self, params: dict[str, Any]) -> dict[str, Any]:
        found = await self._listing(params["listing_id"])
        now = time.time_ns()
        spec = SignalSpec(
            params["atr_period"], round(params["multiplier"] * 1000), params["basis"]
        )
        data = await load_series(self.gateway, found, params["timeframe"], now)
        replay = await load_signal(self.gateway, data, spec, now)
        shown = [
            bar
            for bar in data.final_bars
            if bar["open_time"] >= range_start(params["range"], now)
        ]
        if not shown:
            raise MarketsUnavailable("no final bars in this range to share")
        window = {
            "from_open": shown[0]["open_time"],
            "to_close": shown[-1]["close_time"],
            "bars": len(shown),
        }
        claims = [
            {"text": note["text"], "author": "person", "sources": note["sources"]}
            for note in params["notes"]
        ]
        draft = build_draft(replay, spec.wire(), window, params["layers"], claims, now)
        created = await create_share(
            self.gateway,
            self.caller,
            draft,
            now // 1_000_000,
            params["hours"],
            self.idempotency_key,
        )
        return {**created, "path": f"/apps/markets/share/{created['lease_id']}"}

    async def shared(self, lease_id: str) -> dict[str, Any]:
        lease, record = await read_share(
            self.gateway, self.caller, lease_id, time.time_ns() // 1_000_000
        )
        draft = record["draft"]
        listing = await self._listing(draft["key"]["series"]["listing_id"])
        timeframe = next(
            (
                code
                for code, wire in TIMEFRAMES.items()
                if wire == draft["key"]["series"]["timeframe"]
            ),
            None,
        )
        if timeframe is None:
            raise MarketsUnavailable("snapshot timeframe unavailable")
        kind = draft["spec"]["kind"]
        spec = SignalSpec(kind["atr_period"], kind["multiplier_milli"], kind["basis"])
        as_of = int(draft["created_at"])
        data = await load_series(self.gateway, listing, timeframe, as_of, as_of)
        request = ChartRequest(
            timeframe,
            draft["window"]["from_open"],
            900,
            frozenset(draft["layers"]),
            spec,
            as_of,
        )
        view, _ = await build_chart(self.gateway, data, request)
        return {
            "snapshot": record,
            "listing": listing.public(),
            "chart": view,
            "reproduced": view["state"]["source_revision"] == draft["source_revision"],
            "expires_at": lease["expires_at_ms"],
            "can_revoke": (lease.get("grant") or {}).get("issued_by")
            == self.caller.actor_ref,
        }

    async def revoke(self, lease_id: str) -> dict[str, bool]:
        if not await revoke_share(
            self.gateway, self.caller, lease_id, time.time_ns() // 1_000_000
        ):
            raise PermissionError("only the issuer can revoke this share")
        return {"revoked": True}

    async def call(self, op_id: str, params: dict[str, Any]) -> Any:
        if op_id == "markets.status":
            available = self.gateway.supports_markets()
            return {
                "available": available,
                "detail": None
                if available
                else "The engine does not serve market signals yet",
            }
        if op_id == "markets.listings.list":
            return await self.listings(
                params["q"], params["asset_class"], params["limit"]
            )
        if op_id == "markets.chart.get":
            return await self.chart(params)
        if op_id == "markets.macro.events":
            return {"events": await macro_events(self.gateway)}
        if op_id == "markets.scanner.run":
            return await self.scanner(params)
        if op_id == "markets.snapshots.share":
            return await self.share(params)
        if op_id == "markets.snapshots.get":
            return await self.shared(params["lease_id"])
        if op_id == "markets.snapshots.revoke":
            return await self.revoke(params["lease_id"])
        raise MarketsUnavailable(f"{op_id} has no bound service")
