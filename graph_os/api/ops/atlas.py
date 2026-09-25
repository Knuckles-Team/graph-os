"""Atlas source catalog operations with an explicit authoritative provider port.

The source catalog is not the MCP fleet catalog.  A serving composition must
bind a tenant-aware source provider; an absent binding is unavailable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import Composite, Idempotency, OpSpec, Verb


class CatalogParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Availability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    reason: str | None = None
    observed_at: str


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    reason: str | None = None
    profile_ref: str | None = None


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str | None = None
    availability: Availability
    query_modes: list[str]
    capabilities: list[str]
    connection: Connection | None = None


class CatalogResult(BaseModel):
    """The Atlas browser's existing catalog response, with no synthetic rows."""

    model_config = ConfigDict(extra="forbid")
    catalog_version: str = Field(min_length=1)
    observed_at: str = Field(min_length=1)
    providers: list[Provider]


async def catalog_handler(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Read through a caller-bound catalog port and reject incomplete data."""

    CatalogParams.model_validate(params)
    source_catalog = context.services.get("atlas_source_catalog")
    if source_catalog is None:
        raise RuntimeError("Atlas source catalog authority is unavailable")
    result = await source_catalog.list_providers(context.caller)
    return CatalogResult.model_validate(result).model_dump(
        mode="json", exclude_none=True
    )


def specs() -> tuple[OpSpec, ...]:
    """Alias two UI views to the same authoritative catalog projection."""

    return tuple(
        OpSpec(
            id=op_id,
            verb=Verb.FIND,
            summary="List governed Atlas source providers for the verified caller.",
            examples=("Show my Atlas source providers",),
            params=CatalogParams,
            result=CatalogResult,
            binding=Composite(handler="graph_os.api.ops.atlas.catalog_handler"),
            scopes=frozenset({"source:ingest"}),
            idempotency=Idempotency.NATURAL,
        )
        for op_id in ("atlas.providers.list", "atlas.sources.list")
    )
