"""Independent semantic-pack provider composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agent_connector_sdk.mcp.content import ConnectorContent
from agent_connector_sdk.runner.provisioning import (
    ProvisionOutcome,
    provision_connector_content,
)

from graph_os.content import connector_content

ContentProvider = Callable[[], ConnectorContent]
AttachPack = Callable[[str], Awaitable[Any]]
ReprojectPack = Callable[[str], Awaitable[Any]]


class SemanticContentNotReadyError(RuntimeError):
    """A required ConnectorPack head is absent, stale, or unattached."""


@dataclass(frozen=True, slots=True)
class SemanticProvisionReceipt:
    """Unmodified receipts from the three owning authorities."""

    connector: str
    import_outcome: ProvisionOutcome
    reproject_receipt: Any
    attach_receipt: Any


def default_content_providers() -> tuple[ContentProvider, ...]:
    """Return the independently owned providers in this deployment."""

    from agent_utilities.content import agent_utilities_content

    return (connector_content, agent_utilities_content)


def required_content_connectors() -> tuple[str, ...]:
    """Return the independently declared connector identities required at startup."""

    connectors = tuple(provider().connector for provider in default_content_providers())
    if len(set(connectors)) != len(connectors):
        raise SemanticContentNotReadyError(
            "semantic content providers do not have unique connector identities"
        )
    return connectors


async def verify_semantic_content(
    *,
    client: Any,
    tenant_id: str,
    graph: str,
    connectors: Sequence[str],
    status_reader: Callable[..., Awaitable[Any]] | None = None,
    schema_reader: Callable[..., Awaitable[Any]] | None = None,
) -> None:
    """Prove current pack heads are projected and attached to ``graph``.

    This is the serving-plane operation: it performs only generated EG reads.
    Provisioning and ``AttachPack`` remain an explicit deployment operation.
    """

    from epistemic_graph.generated.connector_pack import ConnectorPackStatusRequest

    if schema_reader is None:
        from epistemic_graph.generated import reasoning

        schema_reader = reasoning.send_graph_schema_list  # type: ignore[attr-defined]
    if status_reader is None:
        from epistemic_graph.generated.storage import send_connector_pack_status

        status_reader = send_connector_pack_status

    expected = tuple(connectors)
    if not expected or len(set(expected)) != len(expected):
        raise SemanticContentNotReadyError(
            "required semantic connector identities are empty or duplicated"
        )
    view = await schema_reader(client, {}, graph)
    attached = {
        source.source_id: source
        for source in view.dynamic_sources
        if source.source_id.startswith("pack:")
    }
    for connector in expected:
        status = await status_reader(
            client,
            ConnectorPackStatusRequest(connector=connector, tenant_id=tenant_id),
            graph,
        )
        if status.head is None:
            raise SemanticContentNotReadyError(
                f"required semantic pack {connector!r} has no committed head"
            )
        projection = status.projection
        if (
            getattr(projection, "projection", None) != "applied"
            or getattr(projection, "graph", None) != graph
        ):
            raise SemanticContentNotReadyError(
                f"required semantic pack {connector!r} is not projected"
            )
        source = attached.get(f"pack:{connector}")
        origin = None if source is None else source.origin
        if (
            source is None
            or getattr(origin, "origin", None) != "pack"
            or getattr(origin, "connector", None) != connector
            or getattr(origin, "record_id", None) != status.head.record_id
            or not source.shapes_sha256
        ):
            raise SemanticContentNotReadyError(
                f"required semantic pack {connector!r} is absent or stale"
            )


async def provision_semantic_content(
    providers: Sequence[ContentProvider],
    *,
    sink: Any,
    reproject_pack: ReprojectPack,
    attach_pack: AttachPack,
) -> tuple[SemanticProvisionReceipt, ...]:
    """Import and attach each provider under its own connector identity.

    ``sink`` is the already-authenticated SDK EpistemicGraph sink and
    ``attach_pack`` is the GraphSchema application port. This function owns no
    credentials, mutation context, pack DTO, or SHACL interpretation.
    """

    contents = tuple(provider() for provider in providers)
    connectors = tuple(content.connector for content in contents)
    if len(set(connectors)) != len(connectors):
        raise ValueError("semantic content providers must have unique connectors")
    receipts: list[SemanticProvisionReceipt] = []
    for content in contents:
        outcome = await provision_connector_content(content, sink=sink)
        reproject_receipt = await reproject_pack(content.connector)
        attach_receipt = await attach_pack(content.connector)
        receipts.append(
            SemanticProvisionReceipt(
                connector=content.connector,
                import_outcome=outcome,
                reproject_receipt=reproject_receipt,
                attach_receipt=attach_receipt,
            )
        )
    return tuple(receipts)


__all__ = [
    "AttachPack",
    "ContentProvider",
    "ReprojectPack",
    "SemanticProvisionReceipt",
    "SemanticContentNotReadyError",
    "default_content_providers",
    "provision_semantic_content",
    "required_content_connectors",
    "verify_semantic_content",
]
