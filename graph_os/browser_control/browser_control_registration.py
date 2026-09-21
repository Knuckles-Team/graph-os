"""Durable browser capability registration authority."""

from __future__ import annotations

import hashlib
from typing import Any

from graph_os.browser_control.browser_control_binding import (
    BINDING_PROPERTY_NAMES,
    BindingReferences,
    binding_properties,
    descriptor_catalog_digest,
    descriptor_tool_scope_digest,
)
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor


def _one_row(authority: Any, query: str, node_id: str) -> dict[str, Any] | None:
    rows = authority.query_cypher(query, {"id": node_id})
    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
        return rows[0]
    return None


def _document_node(refs: BindingReferences) -> str:
    value = f"{refs.tenant_reference}\x00{refs.document_reference}".encode()
    return "browser_document:" + hashlib.sha256(value).hexdigest()


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


def _read_document(authority: Any, node_id: str) -> dict[str, Any] | None:
    names = (
        *BINDING_PROPERTY_NAMES,
        "registration_id",
        "previous_registration_id",
        "status",
    )
    returns = ", ".join(f"d.{name} AS {name}" for name in names)
    query = f"MATCH (d:BrowserControlDocument {{id: $id}}) RETURN {returns} LIMIT 2"
    return _one_row(authority, query, node_id)


def _ensure_registration(
    authority: Any,
    registration_id: str,
    registration_props: dict[str, Any],
) -> None:
    if authority.create_node_if_absent(registration_id, properties=registration_props):
        return
    names = (*BINDING_PROPERTY_NAMES, "descriptors", "status")
    returns = ", ".join(f"r.{name} AS {name}" for name in names)
    row = _one_row(
        authority,
        f"MATCH (r:BrowserControlRegistration {{id: $id}}) RETURN {returns} LIMIT 2",
        registration_id,
    )
    expected = {
        key: value
        for key, value in registration_props.items()
        if key not in {"id", "node_type", "status"}
    }
    if (
        row is None
        or row.get("status") not in {"pending", "published"}
        or any(row.get(key) != value for key, value in expected.items())
    ):
        raise RuntimeError("browser registration authority collision")


def _retire_registration(
    authority: Any,
    previous_registration: str,
    registration_id: str,
    *,
    expected_statuses: tuple[str, ...] = ("published",),
    missing_ok: bool = False,
) -> None:
    for status in expected_statuses:
        retired = authority.compare_and_set_node_fields(
            previous_registration,
            {"status": status},
            {"status": "retired", "retired_by": registration_id},
        )
        if retired:
            return
    row = _one_row(
        authority,
        "MATCH (r:BrowserControlRegistration {id: $id}) "
        "RETURN r.status AS status, r.retired_by AS retired_by LIMIT 2",
        previous_registration,
    )
    already_retired = row is not None and row.get("status") == "retired"
    if row is None and missing_ok:
        return
    if not already_retired:
        raise RuntimeError("prior browser registration was not retired")


def _activate_document(
    authority: Any,
    *,
    document_id: str,
    active_props: dict[str, Any],
    refs: BindingReferences,
    catalog_digest: str,
    tool_scope_digest: str,
    registration_id: str,
) -> str:
    if authority.create_node_if_absent(document_id, properties=active_props):
        return ""
    current = _read_document(authority, document_id)
    if current is None:
        raise RuntimeError("browser document authority is unavailable")
    comparable = {
        **binding_properties(refs),
        "catalog_digest": catalog_digest,
        "tool_scope_digest": tool_scope_digest,
    }
    if all(current.get(key) == value for key, value in comparable.items()):
        previous_registration = str(current.get("previous_registration_id") or "")
        return previous_registration
    immutable = (
        "tenant_reference",
        "actor_reference",
        "login_session_reference",
        "principal_reference",
        "browser_session_reference",
        "origin_reference",
        "document_reference",
        "policy_version",
    )
    _validate_document_authority(current, comparable, immutable)
    return _advance_document(
        authority,
        document_id=document_id,
        current=current,
        comparable=comparable,
        immutable=immutable,
        registration_id=registration_id,
    )


def _validate_document_authority(
    current: dict[str, Any],
    comparable: dict[str, Any],
    immutable: tuple[str, ...],
) -> None:
    if any(current.get(key) != comparable[key] for key in immutable):
        raise PermissionError("browser document binding cannot change authority")


def _advance_document(
    authority: Any,
    *,
    document_id: str,
    current: dict[str, Any],
    comparable: dict[str, Any],
    immutable: tuple[str, ...],
    registration_id: str,
) -> str:
    conditions = {name: current.get(name) for name in current if name != "id"}
    updates = {
        **{key: value for key, value in comparable.items() if key not in immutable},
        "registration_id": registration_id,
        "previous_registration_id": str(current.get("registration_id") or ""),
        "status": "active",
    }
    if not authority.compare_and_set_node_fields(document_id, conditions, updates):
        raise RuntimeError("browser registration generation changed concurrently")
    return updates["previous_registration_id"]


def register_catalog(
    authority: Any,
    refs: BindingReferences,
    tools: tuple[BrowserToolDescriptor, ...],
) -> str:
    """Publish one generation and atomically advance its active document pointer."""

    digest = descriptor_catalog_digest(tools)
    tool_scope_digest = descriptor_tool_scope_digest(tools)
    registration_id = _registration_node(refs, digest)
    descriptors = [
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
    registration_props = {
        "id": registration_id,
        "node_type": "BrowserControlRegistration",
        **binding_properties(refs),
        "catalog_digest": digest,
        "tool_scope_digest": tool_scope_digest,
        "descriptors": descriptors,
        "status": "pending",
    }
    _ensure_registration(authority, registration_id, registration_props)

    document_id = _document_node(refs)
    active_props = {
        "id": document_id,
        "node_type": "BrowserControlDocument",
        **binding_properties(refs),
        "catalog_digest": digest,
        "tool_scope_digest": tool_scope_digest,
        "registration_id": registration_id,
        "status": "active",
    }
    previous_registration = _activate_document(
        authority,
        document_id=document_id,
        active_props=active_props,
        refs=refs,
        catalog_digest=digest,
        tool_scope_digest=tool_scope_digest,
        registration_id=registration_id,
    )
    if previous_registration and previous_registration != registration_id:
        _retire_registration(authority, previous_registration, registration_id)
    activated = authority.compare_and_set_node_fields(
        registration_id, {"status": "pending"}, {"status": "published"}
    )
    if not activated:
        row = _one_row(
            authority,
            "MATCH (r:BrowserControlRegistration {id: $id}) "
            "RETURN r.status AS status LIMIT 2",
            registration_id,
        )
        if row is None or row.get("status") != "published":
            raise RuntimeError("browser registration could not be activated")
    return digest


def retire_catalog(
    authority: Any, refs: BindingReferences, catalog_digest: str
) -> None:
    """Idempotently retire the exact registration for a closed channel."""

    registration_id = _registration_node(refs, catalog_digest)
    document = _read_document(authority, _document_node(refs))
    expected_document = {
        **binding_properties(refs),
        "catalog_digest": catalog_digest,
        "tool_scope_digest": refs.tool_scope_digest,
        "registration_id": registration_id,
    }
    previous = (
        str(document.get("previous_registration_id") or "")
        if document is not None
        and all(document.get(key) == value for key, value in expected_document.items())
        else ""
    )
    _retire_registration(
        authority,
        registration_id,
        "channel_disconnected",
        expected_statuses=("pending", "published"),
        missing_ok=True,
    )
    if previous and previous != registration_id:
        _retire_registration(
            authority,
            previous,
            "channel_disconnected",
            expected_statuses=("pending", "published"),
        )


def active_registration_matches(
    authority: Any,
    refs: BindingReferences,
    catalog_digest: str,
    tool_scope_digest: str,
) -> bool:
    """Confirm every persisted active document binding still matches."""

    current = _read_document(authority, _document_node(refs))
    expected = {
        **binding_properties(refs),
        "catalog_digest": catalog_digest,
        "tool_scope_digest": tool_scope_digest,
        "status": "active",
    }
    if current is None or any(
        current.get(key) != value for key, value in expected.items()
    ):
        return False
    registration_id = current.get("registration_id")
    if not isinstance(registration_id, str) or not registration_id:
        return False
    names = (*BINDING_PROPERTY_NAMES, "catalog_digest", "tool_scope_digest", "status")
    returns = ", ".join(f"r.{name} AS {name}" for name in names)
    row = _one_row(
        authority,
        f"MATCH (r:BrowserControlRegistration {{id: $id}}) RETURN {returns} LIMIT 2",
        registration_id,
    )
    registration_expected = {**expected, "status": "published"}
    return row is not None and all(
        row.get(key) == value for key, value in registration_expected.items()
    )


__all__ = ["active_registration_matches", "register_catalog", "retire_catalog"]
