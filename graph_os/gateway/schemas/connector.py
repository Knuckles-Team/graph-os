"""Typed contracts for the gateway connector REST routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorRunRequest(BaseModel):
    """Request body for ``POST /connector/run``."""

    model_config = ConfigDict(extra="allow")

    source_type: str = Field(
        default="", description="Connector type used for the ingestion run."
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Connector-specific configuration; may contain credentials.",
    )
    connector_id: str = Field(
        default="", description="Stable id for incremental checkpoint storage."
    )
    contextual: bool = Field(
        default=True, description="Enable contextual-retrieval enrichment."
    )
    incremental: bool = Field(
        default=True, description="Use resumable incremental ingestion."
    )


class ConnectorRunResult(BaseModel):
    """Connector-dependent ingestion result."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(description="Ingestion run status.")
    error: str | None = Field(description="Failure detail, else null.")
    nodes_created: int = Field(description="KG nodes created by this run.")
    edges_created: int = Field(description="KG edges created by this run.")


class ConnectorRunResponse(BaseModel):
    """Response envelope for ``POST /connector/run``."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = Field(description="Always success on this path.")
    result: ConnectorRunResult = Field(description="The connector run result.")


class ConnectorSourcesResult(BaseModel):
    """Registered connector-type inventory."""

    model_config = ConfigDict(extra="forbid")

    connectors: list[str] = Field(description="Registered connector type names.")


class ConnectorSourcesResponse(BaseModel):
    """Response envelope for ``GET /connector/sources``."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = Field(description="Always success on this path.")
    result: ConnectorSourcesResult = Field(description="Connector type inventory.")
