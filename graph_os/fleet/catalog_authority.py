"""Verified GraphOS writer for one mounted MCP child catalog observation.

The same generated EG tenant client that supplied the joined fleet read sends
the reconciliation. EG remains the only issuer of the five-field binding.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from epistemic_graph.generated import connector_pack as generated_pack
from epistemic_graph.generated.connector_pack import (
    AgentLibraryMutationContext,
    McpCatalogSnapshotBinding,
)
from epistemic_graph.generated.storage import send_connector_pack

from graph_os.fleet.catalog_reader import ReadContext
from graph_os.fleet.catalog_snapshot import McpCatalogAttestation
from graph_os.fleet.epistemic_adapter import GeneratedFleetCatalogPort


class CatalogAuthorityUnavailable(RuntimeError):
    """The verified source, writer identity, or EG-issued binding is unavailable."""


_DIGEST = re.compile(r"(?:sha256:)?([0-9a-f]{64})\Z")
_OWNER_PRINCIPAL = re.compile(r"principal:sha256:[0-9a-f]{64}\Z")


def _wire_digest(value: str) -> str:
    """Convert an exact observation digest to EG's generated Digest256 form."""
    match = _DIGEST.fullmatch(value)
    if match is None:
        raise CatalogAuthorityUnavailable("mounted catalog digest is malformed")
    return match.group(1)


class MountedCatalog(Protocol):
    def catalog_attestation(self, server_name: str) -> McpCatalogAttestation: ...


class VerifiedAgentLibraryOwnerPrincipal:
    """Read EG's actual owner through the fleet reader's verified tenant client.

    EG additionally requires pack-control and catalog-attest grants and a
    persisted catalog row for this same tenant, server and attester identity.
    """

    def __init__(
        self,
        *,
        fleet_port: GeneratedFleetCatalogPort,
        mounted_catalog: MountedCatalog,
        current_context: Callable[[], ReadContext],
    ) -> None:
        if not callable(current_context):
            raise TypeError("verified EG context provider is required")
        self._fleet_port = fleet_port
        self._mounted_catalog = mounted_catalog
        self._current_context = current_context

    async def serving_principal(self, server_name: str, tenant_id: str) -> str:
        read = await self._fleet_port.read_context()
        observation = self._mounted_catalog.catalog_attestation(server_name)
        if (
            self._current_context() != read
            or tenant_id != read.tenant_id
            or observation.server_name != server_name
            or observation.discovery_tenant != tenant_id
            or observation.attester_principal_id != read.principal_id
        ):
            raise CatalogAuthorityUnavailable("verified EG owner read identity changed")
        request_type = getattr(generated_pack, "McpCatalogAuthorityStatusRequest", None)
        if request_type is None:
            raise CatalogAuthorityUnavailable(
                "generated EG owner read contract is unavailable"
            )
        request = request_type.model_validate(
            {"tenant_id": tenant_id, "server_name": server_name}
        )
        result = await send_connector_pack(
            self._fleet_port.tenant_client,
            {
                "op": {
                    "op": "catalog_owner_principal",
                    "request": request.model_dump(mode="json"),
                }
            },
        )
        if (
            self._current_context() != read
            or self._mounted_catalog.catalog_attestation(server_name) != observation
        ):
            raise CatalogAuthorityUnavailable(
                "mounted child changed during EG owner read"
            )
        principal = result.payload
        if (
            not isinstance(principal, str)
            or _OWNER_PRINCIPAL.fullmatch(principal) is None
        ):
            raise CatalogAuthorityUnavailable("EG owner principal is unavailable")
        return principal


def policy_admitted_catalog_context(
    engine: Any, session: Any
) -> Callable[[str], Awaitable[AgentLibraryMutationContext]]:
    """Mint a reconciler context from AU's independent action policy receipt.

    EG replaces all owner and policy fields with its verified values before
    commit. The request still carries an exact AU effect approval, rather than
    borrowing the pack-import resolver that already needs this binding.
    """
    from agent_utilities.knowledge_graph.core.session import resolve_session
    from agent_utilities.orchestration.action_policy import (
        ActionRequest,
        get_action_policy,
    )

    async def context_for(server_name: str) -> AgentLibraryMutationContext:
        verified = resolve_session(session, required_scope="connector:catalog-attest")
        resolve_session(verified, required_scope="admin:connector-pack")
        actor_id = str(verified.actor.actor_id).strip()
        if not actor_id or not server_name:
            raise CatalogAuthorityUnavailable("catalog attester identity is incomplete")
        request = ActionRequest(
            kind="mcp_catalog_reconcile",
            target=server_name,
            params={"tenant_id": verified.tenant, "scope": "tenant_local"},
            source="graph-os",
            reason="reconcile mounted MCP child catalog",
            actor_id=actor_id,
        )
        decision = get_action_policy(engine).decide(request)
        receipt = decision.receipt
        if (
            not decision.allowed
            or receipt is None
            or not receipt.authorizes_effect
            or receipt.request_digest != request.digest()
            or not str(receipt.receipt_id).strip()
        ):
            raise CatalogAuthorityUnavailable(
                "catalog reconciliation lacks policy approval"
            )
        opaque_caller = (
            "principal:sha256:" + hashlib.sha256(actor_id.encode("utf-8")).hexdigest()
        )
        policy_revision = str(verified.policy_version).strip()
        if not policy_revision:
            raise CatalogAuthorityUnavailable(
                "catalog attester policy revision is absent"
            )
        return AgentLibraryMutationContext(
            request_id=secrets.randbits(63),
            # EG's authenticated handler replaces this with the owner principal.
            principal=opaque_caller,
            caller_principal=opaque_caller,
            attempt_nonce=secrets.token_hex(32),
            tenant_id=verified.tenant,
            actor_scope=opaque_caller,
            purpose_id="mcp-catalog:reconcile",
            policy_revision=policy_revision,
            policy_digest=f"sha256:{receipt.request_digest}",
            policy_decision_id=str(receipt.receipt_id),
            idempotency_key=f"mcp-catalog:{server_name}:pending",
            expected_revision=None,
            trace_id=verified.trace_context,
            created_at_ms=int(time.time() * 1000),
        )

    return context_for


class VerifiedMcpCatalogWriter:
    """Bind a live observation to the fleet reader's exact EG client/identity."""

    def __init__(
        self,
        *,
        fleet_port: GeneratedFleetCatalogPort,
        mounted_catalog: MountedCatalog,
        current_context: Callable[[], ReadContext],
        mutation_context: Callable[[str], Awaitable[AgentLibraryMutationContext]],
    ) -> None:
        if not callable(current_context) or not callable(mutation_context):
            raise TypeError(
                "verified context and mutation context providers are required"
            )
        self._fleet_port = fleet_port
        self._mounted_catalog = mounted_catalog
        self._current_context = current_context
        self._mutation_context = mutation_context

    async def reconcile(
        self, server_name: str, *, expected_catalog_generation: int | None
    ) -> McpCatalogSnapshotBinding:
        read = await self._fleet_port.read_context()
        current = self._current_context()
        if current != read or not read.principal_id or not read.tenant_id:
            raise CatalogAuthorityUnavailable("verified EG catalog identity changed")
        observation = self._mounted_catalog.catalog_attestation(server_name)
        if (
            observation.server_name != server_name
            or observation.discovery_tenant != read.tenant_id
            or observation.attester_principal_id != read.principal_id
        ):
            raise CatalogAuthorityUnavailable(
                "mounted child authority differs from EG reader"
            )
        status_type = getattr(generated_pack, "McpCatalogAuthorityStatusRequest", None)
        if status_type is None:
            raise CatalogAuthorityUnavailable(
                "generated EG catalog status contract is unavailable"
            )
        status_request = status_type.model_validate(
            {"tenant_id": read.tenant_id, "server_name": server_name}
        )
        status = await send_connector_pack(
            self._fleet_port.tenant_client,
            {
                "op": {
                    "op": "catalog_authority_status",
                    "request": status_request.model_dump(mode="json"),
                }
            },
        )
        try:
            previous = (
                None
                if status.payload is None
                else McpCatalogSnapshotBinding.model_validate(status.payload)
            )
        except Exception as exc:
            raise CatalogAuthorityUnavailable(
                "EG scoped catalog status is malformed"
            ) from exc
        persisted_generation = None if previous is None else previous.catalog_generation
        if persisted_generation is not None and persisted_generation < 1:
            raise CatalogAuthorityUnavailable("EG catalog generation is invalid")
        if (
            expected_catalog_generation is not None
            and expected_catalog_generation != persisted_generation
        ):
            raise CatalogAuthorityUnavailable("EG catalog generation changed")
        if (
            self._current_context() != read
            or self._mounted_catalog.catalog_attestation(server_name) != observation
        ):
            raise CatalogAuthorityUnavailable(
                "mounted child changed during status read"
            )
        context = await self._mutation_context(server_name)
        if (
            not isinstance(context, AgentLibraryMutationContext)
            or context.tenant_id != read.tenant_id
        ):
            raise CatalogAuthorityUnavailable(
                "mutation authority differs from EG reader"
            )
        if (
            self._current_context() != read
            or self._mounted_catalog.catalog_attestation(server_name) != observation
        ):
            raise CatalogAuthorityUnavailable(
                "mounted child changed before reconciliation"
            )
        request_type = getattr(generated_pack, "McpCatalogReconcileRequest", None)
        if request_type is None:
            raise CatalogAuthorityUnavailable(
                "generated EG catalog contract is unavailable"
            )
        request = request_type.model_validate(
            {
                "context": context,
                "server_name": observation.server_name,
                "component_id": observation.component_id,
                "component_revision": observation.component_revision,
                "component_digest": _wire_digest(observation.component_digest),
                "registry_revision": observation.registry_revision,
                "registry_digest": _wire_digest(observation.registry_digest),
                "registration_config_digest": _wire_digest(
                    observation.registration_config_digest
                ),
                "four_family_digest": _wire_digest(observation.four_family_digest),
                "child_id": observation.child_id,
                "discovery_tenant": observation.discovery_tenant,
                "local_catalog_epoch": observation.local_catalog_epoch,
                "child_connection_generation": observation.child_connection_generation,
                "expected_catalog_generation": persisted_generation,
            }
        )
        result = await send_connector_pack(
            self._fleet_port.tenant_client,
            {
                "op": {
                    "op": "reconcile_catalog",
                    "request": request.model_dump(mode="json", exclude_none=True),
                }
            },
        )
        # A child or session swap during the await cannot publish an old
        # binding to the SDK, even if EG committed that older observation.
        if (
            self._current_context() != read
            or self._mounted_catalog.catalog_attestation(server_name) != observation
        ):
            raise CatalogAuthorityUnavailable(
                "mounted child changed during reconciliation"
            )
        try:
            binding = McpCatalogSnapshotBinding.model_validate(result.payload)
        except Exception as exc:
            raise CatalogAuthorityUnavailable(
                "EG returned no typed catalog binding"
            ) from exc
        if (
            binding.configuration_revision < 1
            or binding.catalog_generation < 1
            or binding.child_connection_generation
            != observation.child_connection_generation
            or str(binding.snapshot_digest) == "0" * 64
            or str(binding.authorization_scope_digest) == "0" * 64
        ):
            raise CatalogAuthorityUnavailable(
                "EG catalog binding is incomplete or stale"
            )
        return binding


__all__ = [
    "CatalogAuthorityUnavailable",
    "VerifiedMcpCatalogWriter",
    "policy_admitted_catalog_context",
]
