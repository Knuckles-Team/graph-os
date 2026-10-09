"""Strict request/response models for the schema-context query surface
(GRAPHOS-DATA-MARKET-R005, ``specs/data-and-market-projections``).

GDM-02 (``specs/data-and-market-projections/tasks.md``): these models and
``graph_os.gateway.schema_context_service`` are the typed prerequisite for
the not-yet-wired GDM-03 ``POST /graph/schema/context`` REST route and
``graph_schema`` MCP action. Mounting those routes is a separate,
single-owner change; this module does not touch ``kg_server.py`` or
``graph_os.mcp_server.runtime``.

Every field here is taken directly from the design in
``specs/data-and-market-projections/plan.md`` ("Schema context" section),
never invented beyond it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SchemaContextIntent",
    "SchemaContextRequest",
    "SchemaContextResult",
]

SchemaContextIntent = Literal["definition", "joins", "ontology", "owner", "impact"]


class SchemaContextRequest(BaseModel):
    """Request for one table/object's schema context.

    ``extra="forbid"``: the service dispatches only the fields declared
    below; an unrecognized field is a caller mistake, not a silently
    ignored extension point.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_id: str = Field(
        min_length=1,
        max_length=256,
        description="Opaque identifier of the admitted data source to query.",
    )
    schema_name: str = Field(
        min_length=1,
        max_length=256,
        alias="schema",
        description="Database or catalog schema containing the target object.",
    )
    object: str = Field(
        min_length=1,
        max_length=256,
        description="Table, view, or object name to resolve context for.",
    )
    intent: SchemaContextIntent = Field(
        description=(
            "Context facet requested: definition, joins, ontology, owner, or impact."
        ),
    )
    depth: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Bounded dependency-traversal depth for joins/impact.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of facts, proposals, or edges to return.",
    )
    catalog_revision: str | None = Field(
        default=None,
        max_length=256,
        description="Pin the query to a specific approved catalog revision.",
    )


class SchemaContextResult(BaseModel):
    """Projected answer for one schema-context request.

    Every field here is a direct projection of published graph-engine
    catalog state; GraphOS never derives an unverified fact. An inferred
    join or mapping not yet approved belongs in ``proposals``, never in
    ``facts``.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(
        description="Source identifier the answer was resolved against."
    )
    tenant_id: str = Field(description="Tenant that owns the resolved source.")
    object_id: str = Field(description="Stable identifier of the resolved object.")
    catalog_revision: str = Field(
        description="Approved catalog revision the answer was read from."
    )
    as_of: str = Field(
        description="ISO-8601 timestamp the catalog snapshot was read as of."
    )
    facts: list[dict[str, object]] = Field(
        default_factory=list,
        description="Approved, engine-verified context facts for the requested intent.",
    )
    proposals: list[dict[str, object]] = Field(
        default_factory=list,
        description="Unverified or inferred relationships, labelled as proposals.",
    )
    dependency_edges: list[dict[str, object]] = Field(
        default_factory=list,
        description="Bounded dependency/impact edges reachable within the request depth.",
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Record or calculation IDs backing the returned facts.",
    )
    truncated: bool = Field(
        default=False,
        description="True when the result was cut off by the request's limit or depth.",
    )
