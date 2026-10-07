"""Deploy-time provisioning of GraphOS's own semantic content packs.

GraphOS serves the ``graph-os`` and ``agent-utilities`` ConnectorContent packs
in process, so it is a *self-served* catalog producer: epistemic-graph issues
the catalog binding for each pack through ``attest_self_served_catalog``,
pinned to the exact server entry the import then carries. Every step runs under
GraphOS's own verified process session and is idempotent, so this command is
safe to re-run on every deploy:

1. ensure the session's tenant graph exists;
2. ensure each connector's served registration exists in ``__commons__``;
3. per provider: build the pack, obtain EG's binding, import it through the
   SDK sink under AU's policy-gated ``pack_import_authority``;
4. reproject the pack head and attach its schema to the tenant graph;
5. prove the result with :func:`graph_os.semantic_content.verify_semantic_content`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any

from graph_os.semantic_content import (
    ContentProvider,
    default_content_providers,
    verify_semantic_content,
)

COMMONS_GRAPH = "__commons__"
TENANT_GRAPH_TYPE = "Team"
#: The longest lease ``RegisterServer`` accepts; provisioning only needs the
#: registration live while it attests, and the serving process heartbeats it.
REGISTRATION_TTL_SECS = 86_400
_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64

GraphBinder = Callable[[str], AbstractContextManager[object]]


class SemanticProvisioningError(RuntimeError):
    """A provisioning step was refused or answered inconsistently."""


def _bind_session_graph(graph: str) -> AbstractContextManager[object]:
    """Run one engine call under the verified session narrowed to ``graph``."""

    from agent_utilities.api.session import resolve_session, use_session

    return use_session(resolve_session().with_graph(graph))


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def registration_config_digest(url: str, resources: Any) -> str:
    """EG's identity of a registration's served configuration (URL + resources)."""

    return _sha256_json({"resources": resources, "url": url})


def catalog_content_digest(pack: Any) -> str:
    """Digest of every served entry (tools, prompts, resources, templates)."""

    entries = sorted(
        (entry.model_dump(mode="json") for entry in pack.archive.entries),
        key=lambda entry: entry["uri"],
    )
    return _sha256_json(entries)


def _shell_context(tenant_id: str, key: str) -> Any:
    """A request-body context shell; EG re-binds every field from the session."""

    from epistemic_graph.generated.connector_pack import AgentLibraryMutationContext

    return AgentLibraryMutationContext(
        request_id=secrets.randbits(63),
        principal="bound-by-engine",
        caller_principal="bound-by-engine",
        attempt_nonce=secrets.token_hex(32),
        tenant_id=tenant_id,
        actor_scope="bound-by-engine",
        purpose_id="mcp-catalog:reconcile",
        policy_revision="bound-by-engine",
        policy_digest=_PLACEHOLDER_DIGEST,
        policy_decision_id="bound-by-engine",
        idempotency_key=key,
        expected_revision=None,
        trace_id=None,
        created_at_ms=int(time.time() * 1000),
    )


async def ensure_tenant_graph(client: Any, graph: str) -> bool:
    """Create the tenant graph when absent; return whether it was created."""

    from epistemic_graph.generated.cluster import (
        decode_list_graphs,
        send_create_graph,
        send_list_graphs,
    )

    listed = decode_list_graphs(await send_list_graphs(client, {}, graph))
    if any(listing.name == graph for listing in listed):
        return False
    await send_create_graph(
        client,
        {"graph_name": graph, "graph_type": TENANT_GRAPH_TYPE},
        graph,
        idempotency_key=f"graph-os:create-graph:{graph}",
    )
    return True


async def _registry_page(commons: Any) -> Any:
    from epistemic_graph.generated.cluster import send_list_registered_servers

    return await send_list_registered_servers(commons, {"request": {}}, COMMONS_GRAPH)


async def ensure_registrations(
    commons: Any, connectors: Sequence[str], served_url: str
) -> tuple[str, ...]:
    """Register every connector GraphOS serves that has no live registration.

    A live registration is left untouched: its URL and desired state belong to
    the operator, and re-registering would move the registry for no reason.
    """

    from epistemic_graph.generated.cluster import send_register_server

    live = {entry.name for entry in (await _registry_page(commons)).entries}
    registered: list[str] = []
    for connector in connectors:
        if connector in live:
            continue
        await send_register_server(
            commons,
            {
                "name": connector,
                "url": served_url,
                "resources_json": "{}",
                "ttl_secs": REGISTRATION_TTL_SECS,
                "transport": "streamable_http",
                "desired": "enabled",
            },
            COMMONS_GRAPH,
            idempotency_key=f"graph-os:register-server:{connector}:{served_url}",
        )
        registered.append(connector)
    return tuple(registered)


async def attest_self_served_catalog(
    client: Any,
    commons: Any,
    *,
    tenant_id: str,
    graph: str,
    pack: Any,
    bind_graph: GraphBinder,
) -> Any:
    """Ask EG for the binding of ``pack``'s catalog, fenced by the last generation."""

    from epistemic_graph.generated.connector_pack import (
        McpCatalogAuthorityStatusRequest,
        McpSelfServedCatalogAttestRequest,
    )
    from epistemic_graph.generated.storage import (
        send_connector_pack_attest_self_served_catalog,
        send_connector_pack_catalog_authority_status,
    )

    connector = pack.connector
    with bind_graph(COMMONS_GRAPH):
        page = await _registry_page(commons)
    view = next((entry for entry in page.entries if entry.name == connector), None)
    if view is None:
        raise SemanticProvisioningError(
            f"served registration for {connector!r} is not live"
        )
    with bind_graph(graph):
        current = await send_connector_pack_catalog_authority_status(
            client,
            McpCatalogAuthorityStatusRequest(
                tenant_id=tenant_id, server_name=connector
            ),
            graph,
        )
    observed = {
        "connector": connector,
        "server_name": connector,
        "server_entry_digest": pack.archive.server.body.sha256,
        "registry_revision": page.registry_revision,
        "registry_digest": page.registry_digest,
        "registration_config_digest": registration_config_digest(
            view.url, view.resources
        ),
        "four_family_digest": catalog_content_digest(pack),
        "expected_catalog_generation": (
            None if current is None else current.catalog_generation
        ),
    }
    key = f"mcp-catalog:self-served:{connector}:{_sha256_json(observed)[:32]}"
    request = McpSelfServedCatalogAttestRequest(
        context=_shell_context(tenant_id, key), **observed
    )
    with bind_graph(graph):
        return await send_connector_pack_attest_self_served_catalog(
            client, request, graph, idempotency_key=key
        )


async def _owner_principal(
    client: Any, *, tenant_id: str, graph: str, connector: str
) -> str:
    from epistemic_graph.generated.connector_pack import (
        ConnectorPackOpCatalogRequestOwnerPrincipal,
        McpCatalogAuthorityStatusRequest,
    )

    op = ConnectorPackOpCatalogRequestOwnerPrincipal(
        op="catalog_request_owner_principal",
        request=McpCatalogAuthorityStatusRequest(
            tenant_id=tenant_id, server_name=connector
        ),
    )
    payload = await client._send(
        "ConnectorPack", {"op": op.model_dump(mode="json")}, graph
    )
    if not isinstance(payload, str) or not payload.strip():
        raise SemanticProvisioningError("EG owner principal is unavailable")
    return payload


#: How long one connector's automatic projection may take before provisioning
#: re-projects it; the projection worker runs right after the import commits.
PROJECTION_WAIT_SECS = 120.0
_PROJECTION_POLL_SECS = 1.0


async def _projection_state(
    client: Any, *, tenant_id: str, graph: str, connector: str
) -> str:
    from epistemic_graph.generated.connector_pack import ConnectorPackStatusRequest
    from epistemic_graph.generated.storage import send_connector_pack_status

    status = await send_connector_pack_status(
        client,
        ConnectorPackStatusRequest(connector=connector, tenant_id=tenant_id),
        graph,
    )
    return str(getattr(status.projection, "projection", "none"))


async def _await_projection(
    client: Any, *, tenant_id: str, graph: str, connector: str
) -> str:
    """Wait for the import's automatic projection to settle; return its state."""

    deadline = time.monotonic() + PROJECTION_WAIT_SECS
    while True:
        state = await _projection_state(
            client, tenant_id=tenant_id, graph=graph, connector=connector
        )
        if state in {"applied", "failed"} or time.monotonic() >= deadline:
            return state
        await asyncio.sleep(_PROJECTION_POLL_SECS)


async def _reproject(client: Any, *, tenant_id: str, graph: str, connector: str) -> Any:
    context = _shell_context(tenant_id, f"connector-pack:{connector}:reproject")
    return await client._send(
        "ConnectorPack",
        {
            "op": {
                "op": "reproject",
                "request": {
                    "context": context.model_dump(mode="json", exclude_none=True),
                    "connector": connector,
                },
            }
        },
        graph,
        idempotency_key=f"graph-os:reproject:{connector}:{secrets.token_hex(8)}",
    )


async def _attached_at_head(
    client: Any, *, tenant_id: str, graph: str, connector: str
) -> bool:
    from graph_os.semantic_content import SemanticContentNotReadyError

    try:
        await verify_semantic_content(
            client=client, tenant_id=tenant_id, graph=graph, connectors=(connector,)
        )
    except SemanticContentNotReadyError:
        return False
    return True


async def _project_and_attach(
    client: Any, *, tenant_id: str, graph: str, connector: str
) -> Any:
    """Let the import's projection apply, re-project only if it did not, attach."""

    from epistemic_graph.generated.reasoning import send_graph_schema

    state = await _await_projection(
        client, tenant_id=tenant_id, graph=graph, connector=connector
    )
    if state != "applied":
        await _reproject(client, tenant_id=tenant_id, graph=graph, connector=connector)
        state = await _await_projection(
            client, tenant_id=tenant_id, graph=graph, connector=connector
        )
    if state != "applied":
        raise SemanticProvisioningError(
            f"ConnectorPack projection of {connector!r} did not apply ({state})"
        )
    if await _attached_at_head(
        client, tenant_id=tenant_id, graph=graph, connector=connector
    ):
        # Re-attaching the head already attached is refused as a schema-source
        # regression, so an idempotent rerun stops here.
        return None
    return await send_graph_schema(
        client,
        {"op": {"op": "attach_pack", "connector": connector}},
        graph,
        idempotency_key=f"graph-os:attach-pack:{graph}:{connector}:{secrets.token_hex(8)}",
    )


async def provision_semantic_content(
    *,
    engine: Any,
    session: Any,
    served_url: str,
    providers: Sequence[ContentProvider] | None = None,
    bind_graph: GraphBinder = _bind_session_graph,
) -> dict[str, Any]:
    """Run every provisioning step; raise unless the semantic content verifies."""

    from agent_connector_sdk.artifacts.pack import build_connector_content_pack
    from agent_connector_sdk.sinks.epistemic_graph import EpistemicGraphSink
    from agent_utilities.api import pack_import_authority

    tenant_id = str(session.engine_verified_context()["tenant"])
    graph = str(session.graph)
    compute = engine.graph_compute
    client = compute.for_graph(graph).async_client
    commons = compute.for_graph(COMMONS_GRAPH).async_client
    contents = tuple(
        provider() for provider in (providers or default_content_providers())
    )
    connectors = tuple(content.connector for content in contents)
    with bind_graph(graph):
        created = await ensure_tenant_graph(client, graph)
    with bind_graph(COMMONS_GRAPH):
        registered = await ensure_registrations(commons, connectors, served_url)
    imports: dict[str, str] = {}
    for content in contents:
        pack = await build_connector_content_pack(content)
        binding = await attest_self_served_catalog(
            client,
            commons,
            tenant_id=tenant_id,
            graph=graph,
            pack=pack,
            bind_graph=bind_graph,
        )
        with bind_graph(graph):
            owner = await _owner_principal(
                client, tenant_id=tenant_id, graph=graph, connector=pack.connector
            )
            sink = EpistemicGraphSink(
                client,
                pack_import_authority(
                    engine,
                    session.with_graph(graph),
                    catalog_binding=lambda binding=binding: binding,
                    serving_principal=lambda owner=owner: owner,
                ),
            )
            result = await sink.import_pack(pack)
            if getattr(result, "result", None) == "rejected":
                violations = [
                    f"{violation.code.value}:{violation.uri or ''}:{violation.detail}"
                    for violation in getattr(result, "violations", ())
                ]
                raise SemanticProvisioningError(
                    f"ConnectorPack import of {pack.connector!r} was rejected: "
                    + "; ".join(violations)
                )
            await _project_and_attach(
                client, tenant_id=tenant_id, graph=graph, connector=pack.connector
            )
        imports[pack.connector] = str(getattr(result, "result", type(result).__name__))
    with bind_graph(graph):
        await verify_semantic_content(
            client=client, tenant_id=tenant_id, graph=graph, connectors=connectors
        )
    return {
        "graph": graph,
        "graph_created": created,
        "registered": list(registered),
        "imports": imports,
        "verified": True,
    }


__all__ = [
    "SemanticProvisioningError",
    "attest_self_served_catalog",
    "catalog_content_digest",
    "ensure_registrations",
    "ensure_tenant_graph",
    "provision_semantic_content",
    "registration_config_digest",
]
