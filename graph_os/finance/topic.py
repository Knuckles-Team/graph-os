"""The ``finance.flip`` topic on the epistemic-graph broker (EH-416).

A trend flip reaches subscribers as one message on the topic exchange
``finance.flip`` per flip *record* -- an ``emitted`` flip, a revision of one or
a ``retracted`` one, exactly as EG's ``FinanceMarket.signal_replay`` produced
it. The publish is EG's effectively-once ``PublishIdempotent`` keyed on the
record's content-addressed id, so a re-run of the schedule over the same bars
enqueues nothing new: the broker, not this process, remembers what was sent.

Routing keys have five segments, ``<status>.<direction>.<asset>.<timeframe>.
<listing>``, so a subscription binds one ``*``-wildcarded pattern. The payload
is information about a market; it carries no order field and grants nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from graph_os.finance.models import (
    INFORMATIONAL_NOTICE,
    FlipFilter,
    TrackedSeries,
    timeframe_label,
)

__all__ = [
    "FLIP_EXCHANGE",
    "PublishReport",
    "decode_alert",
    "flip_pattern",
    "flip_routing_key",
    "publish_flips",
]

FLIP_EXCHANGE = "finance.flip"
_UNSAFE = re.compile(r"[^A-Za-z0-9_:/-]")


def _segment(value: str) -> str:
    return _UNSAFE.sub("_", value) or "_"


def flip_routing_key(record: dict[str, Any], series: TrackedSeries) -> str:
    """``<status>.<direction>.<asset>.<timeframe>.<listing>`` for one record."""
    flip = record["flip"]
    return ".".join(
        (
            _segment(str(record["status"])),
            _segment(str(flip["to"])),
            series.asset_class,
            timeframe_label(series.timeframe()),
            _segment(series.listing_id),
        )
    )


def flip_pattern(match: FlipFilter) -> str:
    """The binding pattern of a filter; retractions always reach a subscriber."""
    parts = (
        match.direction,
        match.asset_class,
        match.timeframe,
        _segment(match.listing_id) if match.listing_id else None,
    )
    return ".".join(["*", *(part or "*" for part in parts)])


def _payload(record: dict[str, Any], series: TrackedSeries) -> bytes:
    body = {
        "record": record,
        "listing_id": series.listing_id,
        "asset_class": series.asset_class,
        "timeframe": timeframe_label(series.timeframe()),
        "informational_only": True,
        "notice": INFORMATIONAL_NOTICE,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def decode_alert(properties: dict[str, Any]) -> dict[str, Any]:
    """The alert body of one consumed broker message (hex or raw payload)."""
    raw = properties.get("payload", b"")
    data = bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw)
    body = json.loads(data)
    if not isinstance(body, dict) or "record" not in body:
        raise ValueError("not a finance.flip alert")
    return body


@dataclass(frozen=True, slots=True)
class PublishReport:
    published: int = 0
    duplicates: int = 0


async def publish_flips(
    broker: Any, series: TrackedSeries, records: Iterable[dict[str, Any]], now_ms: int
) -> PublishReport:
    """Publish every record once; a record already on the topic is a duplicate."""
    await broker.declare_exchange(FLIP_EXCHANGE, "topic")
    published = duplicates = 0
    for record in records:
        answer = await broker.publish_idempotent(
            FLIP_EXCHANGE,
            flip_routing_key(record, series),
            _payload(record, series),
            producer_id=f"{FLIP_EXCHANGE}:{record['record_id']}",
            seq=1,
            now_ms=now_ms,
        )
        if answer.get("duplicate"):
            duplicates += 1
        else:
            published += 1
    return PublishReport(published=published, duplicates=duplicates)
