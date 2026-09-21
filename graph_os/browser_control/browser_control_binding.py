"""Verified browser identity and authority references."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from agent_utilities.security.persistence_privacy import persistence_reference

from graph_os.browser_control.browser_control_api import BrowserChannelBinding
from graph_os.browser_control.browser_control_common import content_sha256
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor

_CONTROL_CAPABILITIES = (
    "query_cypher",
    "create_node_if_absent",
    "compare_and_set_node_fields",
    "claim_work_item",
    "commit_work_item_result",
    "cancel_work_item",
)


@dataclass(frozen=True, slots=True)
class BindingReferences:
    tenant: str
    tenant_reference: str
    actor_reference: str
    login_session_reference: str
    principal_reference: str
    browser_session_reference: str
    origin_reference: str
    document_reference: str
    route_reference: str
    registration_generation: int
    attended_arm_reference: str
    attended_arm_expires_at: float
    access_token_expires_at: float
    catalog_digest: str
    tool_scope_digest: str
    attended_arm_issued_at: float
    attended_auth_time: float
    attended_acr: str
    attended_issuer: str
    policy_version: str


BINDING_PROPERTY_NAMES = tuple(
    item.name for item in fields(BindingReferences) if item.name != "tenant"
)


def control_authority(engine: Any) -> Any | None:
    """Return the complete native browser-control state authority."""

    authority = getattr(engine, "_work_item_engine", None)
    if authority is None or not all(
        callable(getattr(authority, name, None)) for name in _CONTROL_CAPABILITIES
    ):
        return None
    if not callable(getattr(engine, "batch_typed_mutations", None)) or not callable(
        getattr(engine, "query_cypher", None)
    ):
        return None
    return authority


def binding_references(binding: BrowserChannelBinding) -> BindingReferences:
    """Derive the persisted, non-reversible form of a verified binding."""

    session = binding.session
    tenant = str(session.tenant or "").strip()
    actor_id = str(getattr(session.actor, "actor_id", "") or "").strip()
    tenant_ref = persistence_reference(
        "browser_tenant", tenant, namespace="browser-control"
    )
    actor_ref = persistence_reference("browser_actor", actor_id, namespace=tenant_ref)
    if not tenant or not actor_id:
        raise PermissionError("browser binding requires a verified GraphSession")
    return BindingReferences(
        tenant=tenant,
        tenant_reference=tenant_ref,
        actor_reference=actor_ref,
        login_session_reference=binding.login_session_ref,
        principal_reference=binding.principal_ref,
        browser_session_reference=binding.browser_session_ref,
        origin_reference=persistence_reference(
            "browser_origin", binding.origin, namespace=tenant_ref
        ),
        document_reference=binding.document_ref,
        route_reference=persistence_reference(
            "browser_route", binding.route_id, namespace=binding.document_ref
        ),
        registration_generation=binding.registration_generation,
        attended_arm_reference=binding.attended_arm_ref,
        attended_arm_expires_at=binding.attended_arm_expires_at,
        access_token_expires_at=binding.access_token_expires_at,
        catalog_digest=binding.catalog_digest,
        tool_scope_digest=binding.tool_scope_digest,
        attended_arm_issued_at=binding.attended_arm_issued_at,
        attended_auth_time=binding.attended_auth_time,
        attended_acr=binding.attended_acr,
        attended_issuer=binding.attended_issuer,
        policy_version=str(session.policy_version),
    )


def descriptor_catalog_digest(tools: tuple[BrowserToolDescriptor, ...]) -> str:
    """Return the stable descriptor digest used by registration and leases."""

    descriptors = [
        tool.model_dump(mode="json", exclude={"input_schema", "output_schema"})
        for tool in tools
    ]
    return f"sha256:{content_sha256(descriptors)}"


def descriptor_tool_scope_digest(tools: tuple[BrowserToolDescriptor, ...]) -> str:
    """Return the stable ordered tool/schema capability-scope digest."""

    scope = {
        "tool_ids": [tool.tool_id for tool in tools],
        "schema_digests": {tool.tool_id: tool.schema_digest for tool in tools},
    }
    return f"sha256:{content_sha256(scope)}"


def binding_properties(refs: BindingReferences) -> dict[str, Any]:
    """Return the exact persisted authority fields shared by every record."""

    properties = asdict(refs)
    properties.pop("tenant")
    return properties


__all__ = [
    "BINDING_PROPERTY_NAMES",
    "BindingReferences",
    "binding_properties",
    "binding_references",
    "control_authority",
    "descriptor_catalog_digest",
    "descriptor_tool_scope_digest",
]
