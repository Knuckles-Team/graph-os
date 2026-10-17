"""Run read operations: the current-plan read boundary (ARBORA-OPS-R021.4.5.1).

``runs.current_plan`` serves the ``current_plan`` projection of one run. The
run harness owns the plan scaffold; Arbora owns admission and the served
read. Arbora stores no scaffold and never rebuilds one from stored messages.
An uncomposed reader, an unknown run and a run of another caller all return
the same ``unavailable`` result, which carries no items.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from arbora.api.ops._common import Params, build_read_op
from arbora.api.registry import OpSpec

_RUN_ID = Field(min_length=1, max_length=512)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PlanItem(_Strict):
    """One immutable plan item, in producer order."""

    text: str = Field(min_length=1)
    status: Literal["pending", "in_progress", "done"]


class CurrentPlanSnapshot(_Strict):
    """Immutable plan content and its producer validity interval."""

    run_id: str = _RUN_ID
    revision: int = Field(ge=0)
    observed_at: datetime
    expires_at: datetime
    items: tuple[PlanItem, ...]

    @model_validator(mode="after")
    def _utc_interval(self) -> CurrentPlanSnapshot:
        stamps = (self.observed_at, self.expires_at)
        if any(stamp.utcoffset() != timedelta(0) for stamp in stamps):
            raise ValueError("plan timestamps must use UTC")
        if self.expires_at <= self.observed_at:
            raise ValueError("plan expiry must follow observation")
        return self


class CurrentPlanRead(_Strict):
    """An available plan (possibly empty) or an unavailable read with no items."""

    availability: Literal["available", "unavailable"]
    snapshot: CurrentPlanSnapshot | None = None

    @model_validator(mode="after")
    def _availability_matches(self) -> CurrentPlanRead:
        if (self.availability == "available") != (self.snapshot is not None):
            raise ValueError("plan availability must match snapshot presence")
        return self


UNAVAILABLE = CurrentPlanRead(availability="unavailable")


class RunsCurrentPlanParams(Params):
    """The run to read. Caller authority never comes from parameters."""

    run_id: str = _RUN_ID


class RunsCurrentPlanResult(Params):
    value: dict[str, Any]


@runtime_checkable
class CurrentPlanReader(Protocol):
    """The run harness port that holds the live plan scaffold of a run."""

    async def read(
        self, *, run_id: str, tenant: str, principal: str
    ) -> CurrentPlanRead: ...


def project_current_plan(raw: object, *, run_id: str, now: datetime) -> CurrentPlanRead:
    """Validate a reader result; anything not current for ``run_id`` is unavailable."""
    if not isinstance(raw, CurrentPlanRead):
        return UNAVAILABLE
    try:
        read = CurrentPlanRead.model_validate(raw.model_dump())
    except ValidationError:
        return UNAVAILABLE
    snapshot = read.snapshot
    if snapshot is not None and (
        snapshot.run_id != run_id
        or snapshot.observed_at > now
        or snapshot.expires_at <= now
    ):
        return UNAVAILABLE
    return read


async def read_current_plan(
    context: Any,
    run_id: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CurrentPlanRead:
    """Read one run's plan for the verified caller of ``context``."""
    reader = context.services.get("current_plan_reader")
    if reader is None:
        return UNAVAILABLE
    raw = await reader.read(
        run_id=run_id,
        tenant=context.caller.tenant,
        principal=context.caller.principal,
    )
    return project_current_plan(raw, run_id=run_id, now=clock())


async def handle_runs_current_plan(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return the current plan of one run, or ``unavailable`` with no items."""
    run_id = RunsCurrentPlanParams.model_validate(dict(params)).run_id
    read = await read_current_plan(context, run_id)
    return {"value": read.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        build_read_op(
            op_id="runs.current_plan",
            summary="Read the current plan of one run",
            examples=("what is the current plan of this run",),
            params=RunsCurrentPlanParams,
            result=RunsCurrentPlanResult,
            handler="arbora.api.ops.runs.handle_runs_current_plan",
            scope="kg:read",
        ),
    )


specs = operations

__all__ = [
    "UNAVAILABLE",
    "CurrentPlanRead",
    "CurrentPlanReader",
    "CurrentPlanSnapshot",
    "PlanItem",
    "RunsCurrentPlanParams",
    "RunsCurrentPlanResult",
    "handle_runs_current_plan",
    "operations",
    "project_current_plan",
    "read_current_plan",
    "specs",
]
