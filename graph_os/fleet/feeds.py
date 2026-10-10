"""Typed events for the RUM, security-audit and CI/CD fleet feeds.

GRAPHOS-FLEET-R008 (.1 slice): a typed model per feed kind, each refusing an
unsanitized payload or a missing tenant boundary before it can become a
catalog item. Wiring these into live connector ingestion and the catalog's
``connector_items``/``_pack_annotations`` path is a further slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from graph_os.fleet.catalog_items import PACK_ANNOTATION_FIELDS

FeedKind = Literal["rum", "security_audit", "cicd"]

#: Field names that must never reach a catalog item unredacted.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "cookie",
        "set-cookie",
    }
)


class FeedEventRejected(ValueError):
    """A feed event failed sanitization or tenant-boundary validation."""


def _check_sanitized(payload: dict[str, Any]) -> None:
    for key in payload:
        if key.lower() in _SENSITIVE_KEYS:
            raise FeedEventRejected(f"unsanitized field {key!r} in feed payload")


@dataclass(frozen=True, slots=True)
class FeedEvent:
    """A single sanitized, tenant-scoped event from an approved fleet feed."""

    kind: FeedKind
    tenant: str
    source: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.tenant or not self.tenant.strip():
            raise FeedEventRejected("feed event requires a non-empty tenant")
        if not self.source or not self.source.strip():
            raise FeedEventRejected("feed event requires a non-empty source")
        if not isinstance(self.payload, dict):
            raise FeedEventRejected("feed event payload must be a mapping")
        _check_sanitized(self.payload)


def rum_event(*, tenant: str, source: str, payload: dict[str, Any]) -> FeedEvent:
    """Build a real-user-monitoring feed event, refusing an unsanitized payload."""

    return FeedEvent(kind="rum", tenant=tenant, source=source, payload=payload)


def security_audit_event(
    *, tenant: str, source: str, payload: dict[str, Any]
) -> FeedEvent:
    """Build a security-audit feed event, refusing an unsanitized payload."""

    return FeedEvent(
        kind="security_audit", tenant=tenant, source=source, payload=payload
    )


def cicd_event(*, tenant: str, source: str, payload: dict[str, Any]) -> FeedEvent:
    """Build a CI/CD feed event, refusing an unsanitized payload."""

    return FeedEvent(kind="cicd", tenant=tenant, source=source, payload=payload)


def catalog_annotation_fields() -> tuple[str, ...]:
    """Annotation fields a feed event's catalog entry will need (GRAPHOS-FLEET-R004)."""

    return PACK_ANNOTATION_FIELDS


__all__ = [
    "FeedEvent",
    "FeedEventRejected",
    "FeedKind",
    "catalog_annotation_fields",
    "cicd_event",
    "rum_event",
    "security_audit_event",
]
