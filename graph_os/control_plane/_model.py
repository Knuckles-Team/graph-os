"""Strict value-model base owned by the graph-os control plane."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ControlPlaneModel(BaseModel):
    """Fail closed on unknown or coercible control-plane input."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
