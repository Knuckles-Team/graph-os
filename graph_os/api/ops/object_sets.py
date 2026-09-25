"""Permission-aware object-set search contract for Atlas and ontology clients.

The current EG wheel has no object-set query method.  Serving composition must
bind a caller-scoped implementation with tenant and commons union semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graph_os.api.registry import Composite, Idempotency, OpSpec, Verb


class SearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=8192)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    kind: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=100, ge=1, le=500)


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
