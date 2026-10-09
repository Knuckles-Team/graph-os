"""Tests for the deduplicated flip-alert delivery record
(GRAPHOS-DATA-MARKET-R001; FI-06/FI-07 in
``specs/data-and-market-projections/test-spec.md``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph_os.finance.alerts import (
    FlipAlertDelivery,
    InMemoryAlertDeliveryStore,
    record_alert_delivery,
)


def _delivery(**overrides: object) -> FlipAlertDelivery:
    base: dict[str, object] = {
        "tenant_id": "tenant-a",
        "event_id": "flip-123",
        "subscriber_id": "user-1",
        "channel": "email",
    }
    base.update(overrides)
    return FlipAlertDelivery.model_validate(base)


def test_first_delivery_is_delivered() -> None:
    store = InMemoryAlertDeliveryStore()

    outcome = record_alert_delivery(store, _delivery())

    assert outcome == "delivered"


def test_redelivery_of_the_same_event_is_a_duplicate() -> None:
    """FI-06/FI-07: an at-least-once transport retry or a restart replay of
    the same (tenant, event, subscriber) triple never causes a second
    user-visible delivery."""

    store = InMemoryAlertDeliveryStore()
    record_alert_delivery(store, _delivery())

    second = record_alert_delivery(store, _delivery())

    assert second == "duplicate"


def test_different_subscribers_both_receive_the_same_event() -> None:
    store = InMemoryAlertDeliveryStore()

    first = record_alert_delivery(store, _delivery(subscriber_id="user-1"))
    second = record_alert_delivery(store, _delivery(subscriber_id="user-2"))

    assert first == "delivered"
    assert second == "delivered"


def test_repeated_redelivery_stays_duplicate() -> None:
    store = InMemoryAlertDeliveryStore()
    delivery = _delivery()
    record_alert_delivery(store, delivery)

    outcomes = [record_alert_delivery(store, delivery) for _ in range(5)]

    assert outcomes == ["duplicate"] * 5


def test_an_alert_can_never_carry_an_order_or_portfolio_field() -> None:
    """An alert never authorizes an order or changes a portfolio: the model
    rejects any such field outright rather than silently ignoring it."""

    with pytest.raises(ValidationError):
        _delivery(order_id="order-1")
