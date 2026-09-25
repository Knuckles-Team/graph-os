"""Curated Markets operations projected from the server-side application."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Executor,
    Idempotency,
    OpSpec,
    SubjectRef,
    SubjectSource,
    Verb,
)

Timeframe = Literal["1m", "15m", "1h", "4h", "12h", "1D", "1W", "1M"]
RangeCode = Literal["1M", "3M", "1Y", "5Y", "all"]
Basis = Literal["raw", "heikin_ashi"]
Layer = Literal["trail", "flips", "volume", "atr", "sma200", "macro"]
AssetClass = Literal[
    "crypto", "stock", "etf", "fund", "commodity", "forex", "index", "bond", "other"
]
Status = Literal["warming", "valid", "stale", "unavailable"]
_LISTING = re.compile(r"^(?!.*\.\.)[A-Za-z0-9:._@+-][A-Za-z0-9:._/@+-]{0,255}$")
_LEASE = re.compile(r"^share-[A-Za-z0-9_-]{8,64}$")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    url: str = Field(pattern=r"^https?://", max_length=2048)


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    sources: list[Source] = Field(min_length=1, max_length=16)


class MarketParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(default="", max_length=64)
    listing_id: str = ""
    timeframe: Timeframe = "1D"
    range: RangeCode = "1Y"
    width: int = Field(default=900, ge=16, le=4096)
    layers: list[Layer] = Field(
        default_factory=lambda: ["trail", "flips", "volume"], max_length=6
    )
    atr_period: int = Field(default=10, ge=1, le=200)
    multiplier: float = Field(default=3.0, ge=0.5, le=10.0)
    basis: Basis = "raw"
    asset_class: AssetClass | None = None
    quote: str | None = Field(default=None, max_length=16)
    direction: Literal["bullish", "bearish"] | None = None
    status: list[Status] = Field(default_factory=list)
    flipped_within_days: int | None = Field(default=None, ge=1, le=3650)
    near_ath: bool = False
    limit: int = Field(default=50, ge=1, le=500)
    notes: list[Note] = Field(default_factory=list, max_length=8)
    hours: int = Field(default=24, ge=1, le=24)
    lease_id: str = ""

    @field_validator("listing_id")
    @classmethod
    def _listing_id(cls, value: str) -> str:
        if value and not _LISTING.fullmatch(value):
            raise ValueError("invalid listing id")
        return value

    @field_validator("lease_id")
    @classmethod
    def _lease_id(cls, value: str) -> str:
        if value and not _LEASE.fullmatch(value):
            raise ValueError("invalid share id")
        return value


class MarketResult(BaseModel):
    model_config = ConfigDict(extra="allow")


_OPERATIONS: dict[str, tuple[Verb, Effect, frozenset[str]]] = {
    "status": (Verb.ASK, Effect.READ, frozenset({"compute:finance"})),
    "listings.list": (Verb.FIND, Effect.READ, frozenset({"node:read"})),
    "chart.get": (
        Verb.ASK,
        Effect.READ,
        frozenset({"node:read", "timeseries:read", "compute:finance"}),
    ),
    "macro.events": (Verb.ASK, Effect.READ, frozenset({"node:read"})),
    "scanner.run": (
        Verb.ASK,
        Effect.READ,
        frozenset({"node:read", "timeseries:read", "compute:finance"}),
    ),
    "snapshots.share": (
        Verb.WRITE,
        Effect.WRITE,
        frozenset(
            {
                "node:read",
                "node:write",
                "timeseries:read",
                "compute:finance",
                "lease:write",
            }
        ),
    ),
    "snapshots.get": (
        Verb.ASK,
        Effect.READ,
        frozenset({"node:read", "timeseries:read", "compute:finance", "lease:read"}),
    ),
    "snapshots.revoke": (
        Verb.WRITE,
        Effect.WRITE,
        frozenset({"lease:read", "lease:write"}),
    ),
}


def specs() -> tuple[OpSpec, ...]:
    return tuple(
        OpSpec(
            id=f"markets.{name}",
            verb=verb,
            summary=f"Markets {name.replace('.', ' ')}",
            examples=(f"Markets {name.replace('.', ' ')}",),
            params=MarketParams,
            result=MarketResult,
            binding=Composite(handler="graph_os.api.ops.markets.execute"),
            executor=Executor.SERVICE,
            scopes=frozenset({"finance:read"}),
            executor_scopes=scopes,
            subject=SubjectRef(source=SubjectSource.CALLER_TENANT),
            effect=effect,
            idempotency=Idempotency.KEY_REQUIRED
            if effect is Effect.WRITE
            else Idempotency.NONE,
            audit=AuditClass.EVENT if effect is Effect.WRITE else AuditClass.NONE,
        )
        for name, (verb, effect, scopes) in _OPERATIONS.items()
    )


async def execute(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    from graph_os.finance.markets import MarketsService

    if (
        not context.service_identity
        or "finance:read" not in context.caller.effective_scopes
    ):
        raise PermissionError("markets service authority is unavailable")
    if op.id not in {f"markets.{name}" for name in _OPERATIONS}:
        raise ValueError("unknown Markets operation")
    request = MarketParams.model_validate(params)
    if (
        op.id in {"markets.chart.get", "markets.snapshots.share"}
        and not request.listing_id
    ):
        raise ValueError("listing_id is required")
    if (
        op.id in {"markets.snapshots.get", "markets.snapshots.revoke"}
        and not request.lease_id
    ):
        raise ValueError("lease_id is required")
    service = MarketsService(
        context.client,
        context.caller.tenant,
        context.caller.principal,
        context.idempotency_key,
    )
    return await service.call(op.id, request.model_dump(mode="json"))
