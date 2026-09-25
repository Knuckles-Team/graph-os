"""Curated capacity and fleet throttle operations (EH-604)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    Verb,
)


class CapacityStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cell_id: str | None = Field(default=None, min_length=1, max_length=512)
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=100, ge=1, le=128)


class CapacityCellUpdateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cell: dict[str, object]
    expected_epoch: int | None = Field(default=None, ge=0)


class ThrottleStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    child: str | None = Field(default=None, min_length=1, max_length=256)


class ThrottleModeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    child: str = Field(min_length=1, max_length=256)
    mode: Literal["observe", "enforce"]


class CapacityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: dict[str, object]


_HANDLER = Composite(handler="graph_os.fleet.throttle_service.execute")


def specs() -> tuple[OpSpec, ...]:
    """The intent API's bounded capacity surface."""
    return (
        OpSpec(
            id="capacity.status",
            verb=Verb.ASK,
            summary="Read capacity cells and leases within this tenant.",
            examples=("Show available capacity for this tenant",),
            params=CapacityStatusParams,
            result=CapacityResult,
            binding=_HANDLER,
            scopes=frozenset({"capacity:read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="capacity.cells.update",
            verb=Verb.MANAGE,
            summary="Update a capacity cell with an epoch fence.",
            examples=("Set the reserved floor for this capacity cell",),
            params=CapacityCellUpdateParams,
            result=CapacityResult,
            binding=_HANDLER,
            scopes=frozenset({"capacity:admin"}),
            effect=Effect.ADMIN,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="capacity.throttle.status",
            verb=Verb.ASK,
            summary="Read each fleet child's error budget mode and ceiling.",
            examples=("Which fleet children are currently enforced?",),
            params=ThrottleStatusParams,
            result=CapacityResult,
            binding=_HANDLER,
            scopes=frozenset({"capacity:read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="capacity.throttle.set_mode",
            verb=Verb.MANAGE,
            summary="Set one fleet child to observe or enforce mode.",
            examples=("Enforce the error budget for the github child",),
            params=ThrottleModeParams,
            result=CapacityResult,
            binding=_HANDLER,
            scopes=frozenset({"capacity:admin"}),
            effect=Effect.ADMIN,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
    )
