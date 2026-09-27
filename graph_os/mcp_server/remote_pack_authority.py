"""Request-scoped connector-pack authority for the mounted GraphOS MCP host.

The connector-sync pod calls one authenticated MCP tool with a connector name.
AU authorizes the request actor; only the process attester reconciles its own
mounted child. The two identities are never substituted for each other.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from agent_utilities.api.provisioning import pack_import_authority
from agent_utilities.knowledge_graph.core.session import (
    GraphSession,
    current_session,
    resolve_session,
    use_session,
)
from agent_utilities.security.brain_context import use_actor
from agent_utilities.security.request_identity import actor_from_claims
from epistemic_graph.generated import connector_pack as generated_pack
from epistemic_graph.generated.connector_pack import McpCatalogSnapshotBinding
from epistemic_graph.generated.storage import send_connector_pack

from graph_os.fleet.catalog_authority import CatalogAuthorityUnavailable
from graph_os.fleet.catalog_snapshot import McpCatalogAttestation
from graph_os.mcp_server.bootstrap import _refresh_process_authority


class MountedCatalog(Protocol):
    def catalog_attestation(self, connector: str) -> McpCatalogAttestation: ...

    async def reconcile_pack_catalog_binding(
        self, connector: str
    ) -> McpCatalogSnapshotBinding: ...


def _request_actor_session() -> GraphSession:
    """Require an HTTP bearer minted into the ambient GraphSession.

    GraphOS's ordinary tool scope can fall back to a stdio process session;
    that fallback is forbidden for a remote pack authority response.
    """
    from fastmcp.server.dependencies import get_access_token, get_http_request

    try:
        get_http_request()
        token = get_access_token()
    except Exception as exc:
        raise PermissionError("authenticated remote pack caller required") from exc
    claims = getattr(token, "claims", None)
    session = current_session()
    if not isinstance(claims, Mapping) or session is None:
        raise PermissionError("authenticated remote pack caller required")
    actor = actor_from_claims(dict(claims))
    if actor != session.actor:
        raise PermissionError("remote pack caller differs from verified session")
    return resolve_session(session, required_scope="agent:pack-control")


def _opaque_caller(actor_id: str) -> str:
    if re.fullmatch(r"principal:sha256:[0-9a-f]{64}", actor_id):
        return actor_id
    return "principal:sha256:" + hashlib.sha256(actor_id.encode("utf-8")).hexdigest()


async def _refresh_attester_session(session: GraphSession) -> None:
    await asyncio.to_thread(_refresh_process_authority, session)


class RemoteConnectorPackAuthority:
    """Resolve one pack under separate requester and mounted-child authority."""

    def __init__(
        self,
        *,
        engine: Any,
        attester_session: GraphSession,
        mounted_catalog: MountedCatalog,
        request_session: Callable[[], GraphSession] = _request_actor_session,
    ) -> None:
        if not callable(request_session):
            raise TypeError("verified request-session provider is required")
        self._engine = engine
        self._attester = attester_session
        self._mounted = mounted_catalog
        self._request_session = request_session

    async def __call__(self, connector: str) -> dict[str, Any]:
        requester = self._request_session()
        connector_id = connector.strip() if isinstance(connector, str) else connector
        observed: McpCatalogAttestation | None = None
        issued_binding: McpCatalogSnapshotBinding | None = None

        async def catalog_binding() -> McpCatalogSnapshotBinding:
            nonlocal observed, issued_binding
            # AU calls this only after the caller's policy receipt authorizes
            # the exact connector. Its ambient session is restored immediately
            # after the process-attester reconciliation.
            if requester.tenant != self._attester.tenant:
                raise CatalogAuthorityUnavailable("catalog tenant differs from caller")
            # The process actor has a renewable lease. A captured startup
            # session must be renewed before it is used on later tool calls.
            await _refresh_attester_session(self._attester)
            with use_actor(self._attester.actor), use_session(self._attester):
                resolve_session(
                    self._attester, required_scope="connector:catalog-attest"
                )
                resolve_session(self._attester, required_scope="admin:connector-pack")
                before = self._mounted.catalog_attestation(connector_id)
                if (
                    before.discovery_tenant != requester.tenant
                    or before.server_name != connector_id
                    or before.attester_principal_id
                    != str(self._attester.actor.actor_id)
                ):
                    raise CatalogAuthorityUnavailable("mounted child tenant differs")
                binding = await self._mounted.reconcile_pack_catalog_binding(
                    connector_id
                )
                if self._mounted.catalog_attestation(connector_id) != before:
                    raise CatalogAuthorityUnavailable("mounted child changed")
            observed = before
            issued_binding = binding
            return binding

        async def serving_principal() -> str:
            if observed is None or issued_binding is None:
                raise CatalogAuthorityUnavailable("catalog binding is unavailable")
            verified = resolve_session(requester, required_scope="agent:pack-control")
            if verified.tenant != observed.discovery_tenant:
                raise CatalogAuthorityUnavailable("pack requester tenant changed")
            request_type = getattr(
                generated_pack, "McpCatalogAuthorityStatusRequest", None
            )
            if request_type is None:
                raise CatalogAuthorityUnavailable(
                    "EG owner read contract is unavailable"
                )
            request = request_type.model_validate(
                {"tenant_id": verified.tenant, "server_name": connector_id}
            )
            tenant_client = self._engine.graph_compute.for_graph(
                verified.tenant
            ).async_client
            request_body = request.model_dump(mode="json")
            status = await send_connector_pack(
                tenant_client,
                {"op": {"op": "catalog_binding_status", "request": request_body}},
            )
            try:
                current_binding = McpCatalogSnapshotBinding.model_validate(
                    status.payload
                )
            except Exception as exc:
                raise CatalogAuthorityUnavailable(
                    "EG catalog binding is unavailable"
                ) from exc
            if current_binding != issued_binding:
                raise CatalogAuthorityUnavailable("EG catalog binding changed")
            result = await send_connector_pack(
                tenant_client,
                {
                    "op": {
                        "op": "catalog_request_owner_principal",
                        "request": request_body,
                    }
                },
            )
            refreshed = await send_connector_pack(
                tenant_client,
                {"op": {"op": "catalog_binding_status", "request": request_body}},
            )
            try:
                final_binding = McpCatalogSnapshotBinding.model_validate(
                    refreshed.payload
                )
            except Exception as exc:
                raise CatalogAuthorityUnavailable(
                    "EG catalog binding is unavailable"
                ) from exc
            if (
                final_binding != issued_binding
                or self._mounted.catalog_attestation(connector_id) != observed
            ):
                raise CatalogAuthorityUnavailable("pack catalog changed")
            principal = result.payload
            if (
                not isinstance(principal, str)
                or re.fullmatch(r"principal:sha256:[0-9a-f]{64}", principal) is None
            ):
                raise CatalogAuthorityUnavailable("EG owner principal is unavailable")
            return principal

        resolver = pack_import_authority(
            self._engine,
            requester,
            catalog_binding=catalog_binding,
            serving_principal=serving_principal,
        )
        binding, mutation = await resolver(connector_id)
        verified = resolve_session(requester, required_scope="agent:pack-control")
        if (
            observed is None
            or verified.tenant != observed.discovery_tenant
            or self._mounted.catalog_attestation(connector_id) != observed
            or mutation.tenant_id != verified.tenant
            or mutation.caller_principal != _opaque_caller(str(verified.actor.actor_id))
        ):
            raise CatalogAuthorityUnavailable("pack authority identity changed")
        return {
            "connector": connector_id,
            "catalog_binding": binding.model_dump(mode="json"),
            "mutation_context": mutation.model_dump(mode="json"),
        }


__all__ = ["RemoteConnectorPackAuthority"]
