"""One caller-filtered catalog for the dynamic fleet surface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

ItemKind = Literal[
    "tool", "prompt", "resource", "resource_template", "skill", "connector_item"
]
CredentialMode = Literal["delegated", "service"]
Visible = Callable[["CatalogItem", Any], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """A stable descriptor; body/credentials never enter discovery results."""

    id: str
    kind: ItemKind
    name: str
    description: str = ""
    server: str | None = None
    schema: Mapping[str, Any] = field(default_factory=dict)
    required_scopes: frozenset[str] = frozenset()
    body: str | None = field(default=None, repr=False)
    op: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    annotations: Mapping[str, Any] | None = field(default=None, repr=False)
    effect_override: str | None = field(default=None, repr=False)
    credential_mode: CredentialMode = field(default="delegated", repr=False)
    executor_scopes: frozenset[str] = field(default=frozenset(), repr=False)
    subject_id: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
        }
        if self.server is not None:
            result["server"] = self.server
        if self.schema:
            result["input_schema"] = dict(self.schema)
        if self.op is not None:
            result["op"] = self.op
            result["params"] = dict(self.params)
        return result


def _rank(item: CatalogItem, query: str) -> int:
    terms = query.lower().split()
    text = f"{item.name} {item.description} {item.server or ''}".lower()
    return sum(3 if term in item.name.lower() else 1 for term in terms if term in text)


def _validate_search_request(
    query: str, browse: bool, limit: int, context_budget_tokens: int | None
) -> None:
    if not query and not browse:
        raise ValueError("query or browse=true is required")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be in 1..100")
    if context_budget_tokens is not None and context_budget_tokens < 1:
        raise ValueError("context budget must be positive")


def _passes_filters(
    item: CatalogItem, kind_filter: set[ItemKind], server_filter: set[str], query: str
) -> bool:
    if kind_filter and item.kind not in kind_filter:
        return False
    if server_filter and item.server not in server_filter:
        return False
    return not (query and _rank(item, query) == 0)


def _paginate(
    items: list[CatalogItem], cursor: str | None, limit: int
) -> tuple[list[CatalogItem], str | None]:
    start = 0
    if cursor is not None:
        ids = [item.id for item in items]
        if cursor not in ids:
            raise ValueError("catalog cursor expired")
        start = ids.index(cursor) + 1
    page = items[start : start + limit]
    next_cursor = page[-1].id if start + limit < len(items) and page else None
    return page, next_cursor


class FleetCatalog:
    """Merge EG, child and SDK descriptors through one visibility decision."""

    def __init__(
        self,
        sources: Iterable[Callable[[], Awaitable[Iterable[CatalogItem]]]],
        visible: Visible,
    ):
        self._sources = tuple(sources)
        self._visible = visible

    async def _authorized(self, item: CatalogItem, caller: Any) -> bool:
        scopes = caller.effective_scopes
        return (
            "mcp:discover" in scopes
            and item.required_scopes.issubset(scopes)
            and await self._visible(item, caller)
        )

    async def get(self, item_id: str, caller: Any) -> CatalogItem | None:
        item = await self._raw_get(item_id)
        if item is not None and await self._authorized(item, caller):
            return item
        return None

    async def _raw_get(self, item_id: str) -> CatalogItem | None:
        """Return source metadata only; the caller must apply its own action policy."""
        for source in self._sources:
            for item in await source():
                if item.id == item_id:
                    return item
        return None

    async def search(
        self,
        caller: Any,
        *,
        query: str = "",
        kinds: Iterable[ItemKind] = (),
        servers: Iterable[str] = (),
        browse: bool = False,
        cursor: str | None = None,
        limit: int = 20,
        context_budget_tokens: int | None = None,
    ) -> dict[str, Any]:
        _validate_search_request(query, browse, limit, context_budget_tokens)
        matched = await self._matching_items(caller, query, kinds, servers)
        items = sorted(
            matched.values(), key=lambda item: (-_rank(item, query), item.id)
        )
        if context_budget_tokens is not None:
            limit = min(limit, max(1, context_budget_tokens // 80))
        page, next_cursor = _paginate(items, cursor, limit)
        return {"items": [item.public() for item in page], "next_cursor": next_cursor}

    async def _matching_items(
        self,
        caller: Any,
        query: str,
        kinds: Iterable[ItemKind],
        servers: Iterable[str],
    ) -> dict[str, CatalogItem]:
        kind_filter, server_filter = set(kinds), set(servers)
        matched: dict[str, CatalogItem] = {}
        for source in self._sources:
            for item in await source():
                if not _passes_filters(item, kind_filter, server_filter, query):
                    continue
                if await self._authorized(item, caller):
                    previous = matched.get(item.id)
                    if previous is not None and previous != item:
                        raise ValueError(f"conflicting fleet descriptor: {item.id}")
                    matched[item.id] = item
        return matched


def items_from_eg_catalog(snapshot: Any) -> tuple[CatalogItem, ...]:
    """Project verified AgentComponent records without trusting probe metadata as authority."""
    kinds: dict[str, ItemKind] = {
        "tool": "tool",
        "skill": "skill",
        "mcp_prompt": "prompt",
        "mcp_resource": "resource",
    }
    items: list[CatalogItem] = []
    for server in snapshot.servers:
        for provided in server.provides:
            entry = provided.entry
            kind = kinds.get(entry.kind)
            if kind is None:
                continue
            body = (
                provided.content.body.decode("utf-8")
                if kind in {"skill", "prompt"}
                else None
            )
            items.append(
                CatalogItem(
                    id=f"fleet:{kind}:{entry.server_name}/{entry.upstream_name}",
                    kind=kind,
                    name=entry.upstream_name,
                    description=entry.summary,
                    server=entry.server_name,
                    body=body,
                )
            )
    return tuple(items)


def _probed_item(
    server: str, kind: ItemKind, row: Mapping[str, Any]
) -> CatalogItem | None:
    name = row.get("name") or row.get("uri") or row.get("uriTemplate")
    if not isinstance(name, str) or not name:
        return None
    return CatalogItem(
        id=f"fleet:{kind}:{server}/{name}",
        kind=kind,
        name=name,
        description=str(row.get("description") or ""),
        server=server,
        schema=row.get("inputSchema") or {},
        annotations=(
            dict(row["annotations"])
            if isinstance(row.get("annotations"), Mapping)
            else None
        ),
    )


def items_from_child_probe(
    server: str, info: Mapping[str, Any]
) -> tuple[CatalogItem, ...]:
    """Project live child protocol families; callers still need EG admission."""
    families: tuple[tuple[str, ItemKind], ...] = (
        ("tools", "tool"),
        ("native_prompts", "prompt"),
        ("resources", "resource"),
        ("resource_templates", "resource_template"),
    )
    if info.get("error"):
        return ()
    items = (
        _probed_item(server, kind, row)
        for field_name, kind in families
        for row in info.get(field_name, ())
    )
    return tuple(item for item in items if item is not None)


PACK_ANNOTATION_FIELDS: tuple[str, ...] = (
    "capability",
    "schema_digest",
    "modality",
    "cost",
    "latency",
)


def _pack_annotations(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Required catalog annotations for a fleet pack item (GRAPHOS-FLEET-R004).

    Every connector pack item's catalog entry must carry its capability,
    schema digest, modality, cost and latency so pack items are comparable
    fleet-wide. An entry missing, or emptying, one of these fails closed
    rather than publishing an incomplete catalog row; ``annotations`` may
    carry them as a nested mapping, or each may sit directly on the entry.
    """
    nested = entry.get("annotations")
    source: Mapping[str, Any] = nested if isinstance(nested, Mapping) else entry
    annotations: dict[str, Any] = {}
    for field_name in PACK_ANNOTATION_FIELDS:
        value = source.get(field_name)
        if value is None or (isinstance(value, str) and not value):
            raise ValueError(
                f"connector pack item missing required annotation: {field_name}"
            )
        annotations[field_name] = value
    return annotations


def connector_items(entries: Iterable[Mapping[str, Any]]) -> tuple[CatalogItem, ...]:
    """Expose SDK pack entries as typed operation pointers, never native tools."""
    items: list[CatalogItem] = []
    for entry in entries:
        pack, name, op = entry["pack"], entry["name"], entry["op"]
        items.append(
            CatalogItem(
                id=f"connector:{pack}/{name}",
                kind="connector_item",
                name=name,
                description=entry.get("description", ""),
                op=op,
                params=entry.get("params", {}),
                required_scopes=frozenset(entry.get("required_scopes", ())),
                annotations=_pack_annotations(entry),
            )
        )
    return tuple(items)
