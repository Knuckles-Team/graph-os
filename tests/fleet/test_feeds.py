"""GRAPHOS-FLEET-R008.1: typed RUM/security-audit/CI feed events."""

from __future__ import annotations

import pytest

from graph_os.fleet.feeds import (
    FeedEventRejected,
    cicd_event,
    rum_event,
    security_audit_event,
)


@pytest.mark.spec("GRAPHOS-FLEET-R008.1")
def test_rum_event_accepts_sanitized_payload() -> None:
    event = rum_event(tenant="acme", source="web", payload={"path": "/checkout"})
    assert event.kind == "rum"
    assert event.tenant == "acme"
    assert event.payload == {"path": "/checkout"}


@pytest.mark.spec("GRAPHOS-FLEET-R008.1")
def test_security_audit_event_accepts_sanitized_payload() -> None:
    event = security_audit_event(
        tenant="acme", source="auth-log", payload={"action": "login"}
    )
    assert event.kind == "security_audit"


@pytest.mark.spec("GRAPHOS-FLEET-R008.1")
def test_cicd_event_accepts_sanitized_payload() -> None:
    event = cicd_event(tenant="acme", source="pipeline", payload={"status": "pass"})
    assert event.kind == "cicd"


@pytest.mark.spec("GRAPHOS-FLEET-R008.1")
@pytest.mark.parametrize("builder", [rum_event, security_audit_event, cicd_event])
def test_feed_event_refuses_unsanitized_payload(builder) -> None:
    with pytest.raises(FeedEventRejected):
        builder(tenant="acme", source="x", payload={"api_key": "sk-live-x"})


@pytest.mark.spec("GRAPHOS-FLEET-R008.1")
@pytest.mark.parametrize("builder", [rum_event, security_audit_event, cicd_event])
def test_feed_event_refuses_missing_tenant(builder) -> None:
    with pytest.raises(FeedEventRejected):
        builder(tenant="", source="x", payload={})


@pytest.mark.spec("GRAPHOS-FLEET-R008.1")
@pytest.mark.parametrize("builder", [rum_event, security_audit_event, cicd_event])
def test_feed_event_refuses_non_mapping_payload(builder) -> None:
    with pytest.raises(FeedEventRejected):
        builder(tenant="acme", source="x", payload=None)
