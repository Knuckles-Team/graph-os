"""Register connector access contracts as unapproved virtual mappings.

A fleet connector's ``ontology://`` resources may declare access contracts.
Each contract binds one ontology class to one live source operation.
Onboarding registers one ``SourceConnection`` per source and one unapproved
``VirtualMapping`` per contract. The virtual catalog serves a mapping only
after approval, so registration exposes no data.

The SDK parser ships in ``agent_connector_sdk.access_contract``. Without that
module, onboarding skips contracts and logs at debug level.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

#: SDK access kind to AU virtual-graph source kind.
SOURCE_KIND_BY_ACCESS = {
    "mcp_tool": "mcp",
    "http_endpoint": "api",
    "graphql_query": "graphql",
    "a2a_skill": "a2a",
}
#: Markers that show an ontology body declares an access contract.
CONTRACT_MARKERS = (
    "https://knuckles-team.github.io/agent-connector-sdk/access#",
    "AccessContract",
)
_CATALOG: list[Any] = []


def fleet_virtual_catalog() -> Any:
    """The process virtual catalog that fleet onboarding registers into."""

    if not _CATALOG:
        from agent_utilities.knowledge_graph.virtual_graph import VirtualCatalog

        _CATALOG.append(VirtualCatalog())
    return _CATALOG[0]


def _text(body: object) -> str:
    return body.decode("utf-8") if isinstance(body, bytes) else str(body)


def _entries(pack: Any) -> Iterable[Any]:
    entries = getattr(pack, "entries", None)
    if entries is None:
        entries = getattr(getattr(pack, "archive", None), "entries", ())
    found = entries() if callable(entries) else entries
    return tuple(found or ())


def pack_ontology_resources(pack: Any) -> tuple[tuple[str, str], ...]:
    """The ``(uri, body)`` pair of every ``ontology://`` entry in a pack."""

    return tuple(
        (str(getattr(entry, "uri", "")), _text(getattr(entry, "body", "")))
        for entry in _entries(pack)
        if str(getattr(entry, "uri", "")).startswith("ontology://")
    )


def pack_ontologies(pack: Any) -> tuple[str, ...]:
    """The body of every ``ontology://`` entry in a captured pack."""

    return tuple(body for _, body in pack_ontology_resources(pack))


def declares_contracts(text: str) -> bool:
    """True when an ontology body uses the access-contract vocabulary."""

    return any(marker in text for marker in CONTRACT_MARKERS)


def _parser() -> Any:
    try:
        from agent_connector_sdk.access_contract import parse_access_contracts
    except ImportError:
        logger.debug("connector SDK has no access_contract module; skipping")
        return None
    return parse_access_contracts


def parse_pack_contracts(pack: Any) -> tuple[Any, ...]:
    """Every access contract that a pack's ontologies declare."""

    parse = _parser()
    if parse is None:
        return ()
    found: list[Any] = []
    for uri, text in pack_ontology_resources(pack):
        if declares_contracts(text):
            found.extend(_parse_one(parse, text, uri))
    return tuple(found)


def _parse_one(parse: Any, text: str, uri: str) -> tuple[Any, ...]:
    """Parse one ontology body; log and skip a body the parser rejects."""

    try:
        return tuple(parse(text))
    except ValueError as exc:
        logger.warning(
            "skipping %s: access contracts unparseable: %s",
            uri,
            exc,
        )
        return ()


def _by_source(contracts: Sequence[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for contract in contracts:
        grouped.setdefault(contract.source_id, []).append(contract)
    return grouped


def _entity(contract: Any) -> Any:
    from agent_utilities.knowledge_graph.virtual_graph import DiscoveredEntity

    return DiscoveredEntity(
        name=contract.entity,
        key=contract.key_field,
        fields=tuple(sorted({f for _, f in contract.predicates})),
        operation=contract.operation,
    )


def _unwired_call(operation: str, arguments: Mapping[str, Any]) -> Any:
    raise LookupError(f"no live binding for {operation!r} yet")


def _register_source(
    catalog: Any, connector: str, source_id: str, contracts: Sequence[Any]
) -> None:
    from agent_utilities.knowledge_graph.virtual_graph import (
        MetadataContract,
        OperationAdapter,
        SourceConnection,
    )

    connection = SourceConnection(
        source_id=source_id,
        kind=SOURCE_KIND_BY_ACCESS[contracts[0].access_kind],
        endpoint_ref=f"fleet://{connector}",
    )
    entities = {c.entity: _entity(c) for c in contracts}
    metadata = MetadataContract(
        source_id=source_id,
        schema_version="1",
        entities=tuple(entities.values()),
    )
    adapter = OperationAdapter(connection, metadata, _unwired_call)
    catalog.register(connection, metadata, adapter)


def _add_mappings(catalog: Any, contracts: Sequence[Any]) -> int:
    from agent_utilities.knowledge_graph.virtual_graph import VirtualMapping

    known = {m.mapping_id for m in catalog.mappings}
    added = 0
    for contract in contracts:
        mapping = VirtualMapping(**contract.virtual_mapping_fields())
        if mapping.mapping_id in known:
            continue
        catalog.add_mapping(mapping)
        added += 1
    return added


def register_access_contracts(pack: Any, *, connector: str, catalog: Any = None) -> int:
    """Register a pack's access contracts unapproved; return mappings added."""

    contracts = parse_pack_contracts(pack)
    if not contracts:
        return 0
    target = catalog if catalog is not None else fleet_virtual_catalog()
    added = 0
    for source_id, items in _by_source(contracts).items():
        _register_source(target, connector, source_id, items)
        added += _add_mappings(target, items)
    return added


__all__ = [
    "CONTRACT_MARKERS",
    "SOURCE_KIND_BY_ACCESS",
    "declares_contracts",
    "fleet_virtual_catalog",
    "pack_ontologies",
    "pack_ontology_resources",
    "parse_pack_contracts",
    "register_access_contracts",
]
