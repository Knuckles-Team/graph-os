"""Durable browser capability registration authority.

Every registration generation is two immutable EG ``ControlLease`` records:
the registration itself (kind ``browser.registration``: the exact descriptor
set) and the document's generation pointer (kind ``browser.document``:
``<document>:<generation>`` naming that registration). A registration is valid
only while the current generation's document record points at it and both are
active; the previous generation's records are revoked on advance, and a
registration left unpointed by a crash expires with the attended arm. No
record is ever mutated except by EG's status transitions.
"""

from __future__ import annotations

import hashlib
from typing import Any

from graph_os.browser_control.browser_control_binding import (
    BindingReferences,
    binding_properties,
    descriptor_catalog_digest,
    descriptor_tool_scope_digest,
)
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor
from graph_os.browser_control.browser_control_records import (
    issue_record,
    read_record,
    transition_record,
)

_REGISTRATION_KIND = "browser.registration"
_DOCUMENT_KIND = "browser.document"
_IMMUTABLE_DOCUMENT_FIELDS = (
    "tenant_reference",
    "actor_reference",
    "login_session_reference",
    "principal_reference",
    "browser_session_reference",
    "origin_reference",
    "document_reference",
    "policy_version",
)


def _document_node(refs: BindingReferences) -> str:
    value = f"{refs.tenant_reference}\x00{refs.document_reference}".encode()
    return "browser_document:" + hashlib.sha256(value).hexdigest()


def _document_record(refs: BindingReferences, generation: int) -> str:
    return f"{_document_node(refs)}:{generation}"


def _registration_node(refs: BindingReferences, catalog_digest: str) -> str:
    value = "\x00".join(
        (
            refs.tenant_reference,
            refs.document_reference,
            str(refs.registration_generation),
            refs.attended_arm_reference,
            catalog_digest,
        )
    )
    return "browser_registration:" + hashlib.sha256(value.encode()).hexdigest()


def _descriptors(tools: tuple[BrowserToolDescriptor, ...]) -> list[dict[str, Any]]:
    return [
        {
            "tool_id": tool.tool_id,
            "version": tool.version,
            "schema_digest": tool.schema_digest,
            "mutation_class": tool.mutation_class.value,
            "confirmation_policy": tool.confirmation_policy.value,
            "required_roles": list(tool.required_roles),
            "source_ref": tool.source_ref,
        }
        for tool in tools
    ]


def _matches(record: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    return record is not None and all(
        record.get(key) == value for key, value in expected.items()
    )


def _issue_exact(
    engine: Any,
    refs: BindingReferences,
    *,
    record_id: str,
    kind: str,
    grant: dict[str, Any],
    now: float,
) -> None:
    """Issue ``grant`` once; an existing record must be the same active grant."""
    issue_record(
        engine,
        tenant=refs.tenant,
        record_id=record_id,
        kind=kind,
        grant=grant,
        issued_at=now,
        expires_at=refs.attended_arm_expires_at,
        hard_expires_at=refs.attended_arm_expires_at,
        idempotency_key=f"{kind}:{record_id}",
    )
    current = read_record(engine, tenant=refs.tenant, record_id=record_id, kind=kind)
    if not _matches(current, {**grant, "status": "active"}):
        raise RuntimeError("browser registration authority collision")


def _revoke(engine: Any, refs: BindingReferences, record_id: str, kind: str) -> None:
    """Idempotently revoke one record; a missing or ended record is a no-op."""
    current = read_record(engine, tenant=refs.tenant, record_id=record_id, kind=kind)
    if current is None or current.get("status") in {"revoked", "expired"}:
        return
    if not transition_record(
        engine, tenant=refs.tenant, record_id=record_id, current=current, to="revoked"
    ):
        latest = read_record(engine, tenant=refs.tenant, record_id=record_id, kind=kind)
        if latest is None or latest.get("status") not in {"revoked", "expired"}:
            raise RuntimeError("prior browser registration was not retired")


def _previous_document(
    engine: Any, refs: BindingReferences, comparable: dict[str, Any]
) -> dict[str, Any] | None:
    previous = read_record(
        engine,
        tenant=refs.tenant,
        record_id=_document_record(refs, refs.registration_generation - 1),
        kind=_DOCUMENT_KIND,
    )
    if previous is not None and any(
        previous.get(key) != comparable[key] for key in _IMMUTABLE_DOCUMENT_FIELDS
    ):
        raise PermissionError("browser document binding cannot change authority")
    return previous


def register_catalog(
    engine: Any,
    refs: BindingReferences,
    tools: tuple[BrowserToolDescriptor, ...],
    *,
    now: float,
) -> str:
    """Publish one generation's registration and its document pointer."""

    digest = descriptor_catalog_digest(tools)
    tool_scope_digest = descriptor_tool_scope_digest(tools)
    registration_id = _registration_node(refs, digest)
    comparable = {
        **binding_properties(refs),
        "catalog_digest": digest,
        "tool_scope_digest": tool_scope_digest,
    }
    _issue_exact(
        engine,
        refs,
        record_id=registration_id,
        kind=_REGISTRATION_KIND,
        grant={**comparable, "descriptors": _descriptors(tools)},
        now=now,
    )
    previous = _previous_document(engine, refs, comparable)
    previous_registration = str((previous or {}).get("registration_id") or "")
    _issue_exact(
        engine,
        refs,
        record_id=_document_record(refs, refs.registration_generation),
        kind=_DOCUMENT_KIND,
        grant={
            **comparable,
            "registration_id": registration_id,
            "previous_registration_id": previous_registration,
        },
        now=now,
    )
    if previous is not None:
        _revoke(
            engine,
            refs,
            _document_record(refs, refs.registration_generation - 1),
            _DOCUMENT_KIND,
        )
    if previous_registration and previous_registration != registration_id:
        _revoke(engine, refs, previous_registration, _REGISTRATION_KIND)
    return digest


def retire_catalog(engine: Any, refs: BindingReferences, catalog_digest: str) -> None:
    """Idempotently retire the exact registration for a closed channel."""

    registration_id = _registration_node(refs, catalog_digest)
    document = read_record(
        engine,
        tenant=refs.tenant,
        record_id=_document_record(refs, refs.registration_generation),
        kind=_DOCUMENT_KIND,
    )
    expected_document = {
        **binding_properties(refs),
        "catalog_digest": catalog_digest,
        "tool_scope_digest": refs.tool_scope_digest,
        "registration_id": registration_id,
    }
    previous = (
        str((document or {}).get("previous_registration_id") or "")
        if _matches(document, expected_document)
        else ""
    )
    _revoke(engine, refs, registration_id, _REGISTRATION_KIND)
    if previous and previous != registration_id:
        _revoke(engine, refs, previous, _REGISTRATION_KIND)


def active_registration_matches(
    engine: Any,
    refs: BindingReferences,
    catalog_digest: str,
    tool_scope_digest: str,
) -> bool:
    """Confirm the current generation's document and registration still match."""

    expected = {
        **binding_properties(refs),
        "catalog_digest": catalog_digest,
        "tool_scope_digest": tool_scope_digest,
        "status": "active",
    }
    document = read_record(
        engine,
        tenant=refs.tenant,
        record_id=_document_record(refs, refs.registration_generation),
        kind=_DOCUMENT_KIND,
    )
    if not _matches(document, expected):
        return False
    assert document is not None
    registration_id = document.get("registration_id")
    if not isinstance(registration_id, str) or not registration_id:
        return False
    registration = read_record(
        engine,
        tenant=refs.tenant,
        record_id=registration_id,
        kind=_REGISTRATION_KIND,
    )
    return _matches(registration, expected)


__all__ = ["active_registration_matches", "register_catalog", "retire_catalog"]
