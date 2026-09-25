"""Permission-aware object-set search contract for Atlas and ontology clients.

The current EG wheel has no object-set query method.  Serving composition must
bind a caller-scoped implementation with tenant and commons union semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graph_os.api.registry import Composite, Idempotency, OpSpec, Verb


class SearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = ""
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    kind: str | None = None
    limit: int = Field(default=50, ge=1, le=256)

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 8192:
            raise ValueError("query exceeds the WebUI byte bound")
        return value

    @field_validator("kind")
    @classmethod
    def bounded_kind(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 128:
            raise ValueError("kind exceeds the WebUI byte bound")
        return value


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str]
    rows: list[dict[str, Any]]
    count: int = Field(ge=0)

    @model_validator(mode="after")
    def matching_rows(self) -> SearchResult:
        if self.count != len(self.rows) or self.ids != [
            row.get("id") for row in self.rows
        ]:
            raise ValueError("object-set result IDs and count must match rows")
        return self


class ByLabelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=256)

    @field_validator("label")
    @classmethod
    def bounded_label(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("label exceeds the object type byte bound")
        return value


async def by_label_handler(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Use EG's RLS-projected label union over verified tenant and commons."""

    request = ByLabelParams.model_validate(params)
    tenant = context.caller.tenant
    if not isinstance(tenant, str) or not tenant.strip():
        raise RuntimeError("verified tenant is unavailable")
    graphs = list(dict.fromkeys((tenant, "__commons__")))
    rows = await context.client.nodes.list_by_label_union(
        request.label, graphs, request.limit
    )
    if len(rows) > request.limit:
        raise ValueError("EG label union exceeded the requested limit")
    projected = []
    for node_id, properties in rows:
        if not isinstance(node_id, str) or not isinstance(properties, dict):
            raise ValueError("EG label union returned an invalid node row")
        if "id" in properties and properties["id"] != node_id:
            raise ValueError("EG label union returned a mismatched node id")
        projected.append({**properties, "id": node_id})
    if len({row["id"] for row in projected}) != len(projected):
        raise ValueError("EG label union returned duplicate node ids")
    result = {
        "ids": [row["id"] for row in projected],
        "rows": projected,
        "count": len(projected),
    }
    return SearchResult.model_validate(result).model_dump(mode="json")


async def search_handler(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Delegate only to a verified-caller object-set authority."""

    request = SearchParams.model_validate(params)
    service = context.services.get("object_sets")
    if service is None:
        raise RuntimeError("Object-set authority is unavailable")
    result = await service.search(context.caller, request)
    return SearchResult.model_validate(result).model_dump(mode="json")


def specs() -> tuple[OpSpec, ...]:
    """Unserved full object-set search; not part of the curated registry."""

    return (
        OpSpec(
            id="objects.search",
            verb=Verb.FIND,
            summary="Search caller-visible object sets across accessible graphs.",
            examples=("Find objects of this type matching my filters",),
            params=SearchParams,
            result=SearchResult,
            binding=Composite(handler="graph_os.api.ops.object_sets.search_handler"),
            scopes=frozenset({"kg:read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )


def served_specs() -> tuple[OpSpec, ...]:
    """The supported concrete-label subset, separate from full search."""

    return (
        OpSpec(
            id="objects.by_label",
            verb=Verb.FIND,
            summary="Find caller-visible concrete-label nodes in tenant and commons.",
            examples=("List my Document nodes",),
            params=ByLabelParams,
            result=SearchResult,
            binding=Composite(handler="graph_os.api.ops.object_sets.by_label_handler"),
            scopes=frozenset({"node:read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
