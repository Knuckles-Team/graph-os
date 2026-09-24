"""Typed inputs of the finance surfaces (MCP, REST, console, scheduler)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "INFORMATIONAL_NOTICE",
    "AssetClass",
    "ListingType",
    "FlipFilter",
    "OrderIntent",
    "TrackedSeries",
    "canonical_digest",
    "principal_ref",
    "timeframe_label",
]

#: finance-v1 ``assetClass`` (the closed vocabulary its shape enforces).
AssetClass = Literal[
    "crypto", "stock", "etf", "fund", "commodity", "forex", "index", "bond"
]
ListingType = Literal["spot", "perpetual", "future", "option", "index"]

#: Carried by every alert, scan and explanation: the product is informational.
INFORMATIONAL_NOTICE = (
    "Informational only, not investment advice. Signals are mechanical rules "
    "over market data; agent text is a claim with its sources and may be wrong."
)

_INTERVAL_UNITS = {"m": "minutes", "h": "hours"}
_CALENDAR_UNITS = {"1d": "day", "1w": "week", "1M": "month"}


_LABELS = {"day": "1D", "week": "1W", "month": "1M"}


def timeframe_label(timeframe: dict[str, Any]) -> str:
    """EG's compact label (``15m``, ``4h``, ``1D``, ``1W``, ``1M``)."""
    unit = str(timeframe["unit"])
    if unit in _LABELS:
        return _LABELS[unit]
    return f"{timeframe['n']}{unit[0]}"


def _decimal_step(decimals: int) -> str:
    return "1" if decimals == 0 else "0." + "0" * (decimals - 1) + "1"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def canonical_digest(domain: str, value: Any) -> str:
    """sha256 over a domain tag and the value's canonical JSON."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{domain}\n{encoded}".encode()).hexdigest()


def principal_ref(principal: str) -> str:
    """EG's persistence id of a verified principal (``principal:sha256:<hex>``)."""
    return "principal:sha256:" + hashlib.sha256(principal.encode()).hexdigest()


class TrackedSeries(_Frozen):
    """One listing x timeframe the schedule keeps current."""

    #: The finance-v1 ``Listing`` node id (e.g. ``binance:BTC/USDT:spot``).
    listing_id: str = Field(min_length=1, max_length=256)
    #: The venue's own symbol, as the emerald-exchange connector takes it.
    symbol: str = Field(min_length=1, max_length=64)
    asset_class: AssetClass
    #: The listed and quote instruments and the venue, by symbol / name.
    base: str = Field(min_length=1, max_length=32)
    quote: str = Field(min_length=1, max_length=32)
    venue: str = Field(min_length=1, max_length=64)
    listing_type: ListingType = "spot"
    name: str = Field(default="", max_length=128)
    interval: str = Field(pattern=r"^([1-9][0-9]{0,3}[mh]|1d|1w|1M)$")
    period: str = Field(default="1y", pattern=r"^([1-9][0-9]{0,3}(d|w|mo|y)|max)$")
    price_decimals: int = Field(ge=0, le=12)
    volume_decimals: int = Field(default=0, ge=0, le=12)
    calendar_id: str = "utc-24x7"
    price_basis: str = "trade"
    atr_period: int = Field(default=10, ge=1, le=500)
    multiplier_milli: int = Field(default=3_000, ge=1, le=100_000)
    basis: Literal["raw", "heikin_ashi"] = "raw"

    @property
    def instrument_node(self) -> str:
        return f"finance:instrument:{self.base}"

    @property
    def quote_node(self) -> str:
        return f"finance:instrument:{self.quote}"

    @property
    def venue_node(self) -> str:
        return f"finance:venue:{self.venue}"

    @property
    def series_node(self) -> str:
        """The finance-v1 ``BarSeries`` node id."""
        return f"finance:bars:{self.series_key}"

    @property
    def tsdb_series_id(self) -> str:
        return f"finance.bars.{self.series_key}"

    def tick_size(self) -> str:
        """The price tick as a decimal string (ticks are ``price * 10**d``)."""
        return _decimal_step(self.price_decimals)

    def volume_step(self) -> str:
        return _decimal_step(self.volume_decimals)

    @property
    def series_key(self) -> str:
        """A stable id for this series' bars and signal state."""
        return canonical_digest(
            "graphos/finance/series/v1", [self.listing_id, self.interval]
        )[:32]

    def timeframe(self) -> dict[str, Any]:
        """The EG ``Timeframe`` of this interval."""
        if self.interval in _CALENDAR_UNITS:
            return {"unit": _CALENDAR_UNITS[self.interval]}
        return {
            "unit": _INTERVAL_UNITS[self.interval[-1]],
            "n": int(self.interval[:-1]),
        }

    def identity(self) -> dict[str, Any]:
        """The EG ``SeriesIdentity``."""
        return {
            "listing_id": self.listing_id,
            "price_basis": self.price_basis,
            "timeframe": self.timeframe(),
            "calendar_id": self.calendar_id,
        }

    def spec(self) -> dict[str, Any]:
        """The EG ``IndicatorSpec`` of the trailing-trend signal."""
        return {
            "version": 1,
            "kind": {
                "kind": "super_trend",
                "atr_period": self.atr_period,
                "multiplier_milli": self.multiplier_milli,
                "basis": self.basis,
            },
        }


class FlipFilter(_Frozen):
    """Which flips a subscription receives; an absent field matches any."""

    asset_class: AssetClass | None = None
    timeframe: str | None = Field(default=None, pattern=r"^[0-9]{1,4}[mhDWM]$")
    listing_id: str | None = Field(default=None, max_length=256)
    direction: Literal["bullish", "bearish"] | None = None


class OrderIntent(_Frozen):
    """The order a proposal asks a person to approve. Nothing else is in it."""

    symbol: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _limit_needs_a_price(self) -> OrderIntent:
        if self.order_type in ("limit", "stop_limit") and self.limit_price is None:
            raise ValueError("a limit order needs limit_price")
        return self

    def patch(self) -> dict[str, Any]:
        """The exact field patch the D18 change set will carry."""
        return self.model_dump(mode="json")

    def digest(self) -> str:
        """What the approver is shown and must echo back."""
        return canonical_digest("graphos/finance/order-intent/v1", self.patch())
