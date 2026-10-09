"""Deduplicated flip-alert delivery record (GRAPHOS-DATA-MARKET-R001, FI-06/07).

``FlipAlertDelivery`` is the typed, durable delivery record for one finance
flip event delivered to one subscriber over one channel. ``record_alert_delivery``
is the dedupe core: redelivering the same ``(tenant_id, event_id,
subscriber_id)`` triple -- whether from an at-least-once transport retry or a
restart replay -- returns ``"duplicate"`` and performs no second
user-visible delivery. The model forbids any order- or portfolio-mutating
field, so an alert can never itself authorize an order or change a
portfolio; wiring this record to the finance outbox subscription and real
notification channels is GDM-08 follow-on work.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DeliveryOutcome",
    "FlipAlertDelivery",
    "InMemoryAlertDeliveryStore",
    "AlertDeliveryStore",
    "record_alert_delivery",
]

DeliveryOutcome = str  # "delivered" | "duplicate"


class FlipAlertDelivery(BaseModel):
    """One flip alert delivered to one subscriber over one channel."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=256)
    event_id: str = Field(min_length=1, max_length=256)
    subscriber_id: str = Field(min_length=1, max_length=256)
    channel: str = Field(min_length=1, max_length=128)


class AlertDeliveryStore(Protocol):
    """Durable (tenant_id, event_id, subscriber_id) membership."""

    def seen(self, tenant_id: str, event_id: str, subscriber_id: str) -> bool:
        """True when this subscriber already received this event."""

    def record(self, tenant_id: str, event_id: str, subscriber_id: str) -> None:
        """Durably record that this subscriber received this event."""


class InMemoryAlertDeliveryStore:
    """Reference ``AlertDeliveryStore`` for tests and a single-process default."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, str]] = set()

    def seen(self, tenant_id: str, event_id: str, subscriber_id: str) -> bool:
        return (tenant_id, event_id, subscriber_id) in self._seen

    def record(self, tenant_id: str, event_id: str, subscriber_id: str) -> None:
        self._seen.add((tenant_id, event_id, subscriber_id))


def record_alert_delivery(
    store: AlertDeliveryStore, entry: FlipAlertDelivery
) -> DeliveryOutcome:
    """Deliver one alert exactly once per ``(tenant, event, subscriber)``.

    FI-06/FI-07: calling this twice for the same triple -- an at-least-once
    transport retry, or a redelivery after restart -- returns ``"delivered"``
    once and ``"duplicate"`` on every subsequent call, with no second
    user-visible delivery and no order or portfolio effect either way.
    """

    if store.seen(entry.tenant_id, entry.event_id, entry.subscriber_id):
        return "duplicate"
    store.record(entry.tenant_id, entry.event_id, entry.subscriber_id)
    return "delivered"
