"""Evolution control operations hosted by the single GraphOS daemon."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    Verb,
)


class EvolutionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loop_id: str | None = None
    name: str | None = None
    proposal_id: str | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    max_topics: int = Field(default=5, ge=1, le=20)
    request: dict[str, object] | None = None


class EvolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: object


async def evolution_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    """Use only the explicitly composed daemon control port."""
    control = context.services.get("evolution")
    if control is None or not callable(getattr(control, "execute", None)):
        raise RuntimeError("evolution daemon control is unavailable")
    return {"value": await control.execute(op.id, params)}


def _op(name: str, verb: Verb, effect: Effect) -> OpSpec:
    return OpSpec(
        id=f"evolution.{name}",
        verb=verb,
        summary=f"{name.replace('.', ' ')} in the evolution engine",
        examples=(f"{name.replace('.', ' ')} for this evolution loop",),
        params=EvolutionParams,
        result=EvolutionResult,
        binding=Composite(handler="graph_os.api.ops.evolution.evolution_handler"),
        scopes=frozenset({"loops:read" if effect is Effect.READ else "loops:control"}),
        effect=effect,
        idempotency=Idempotency.NONE
        if effect is Effect.READ
        else Idempotency.KEY_REQUIRED,
        audit=AuditClass.NONE if effect is Effect.READ else AuditClass.EVENT,
    )


def specs() -> tuple[OpSpec, ...]:
    return (
        _op("loops.status", Verb.ASK, Effect.READ),
        _op("loops.run", Verb.ACT, Effect.WRITE),
        _op("loops.pause", Verb.MANAGE, Effect.ADMIN),
        _op("schedules.list", Verb.ASK, Effect.READ),
        _op("schedules.get", Verb.ASK, Effect.READ),
        _op("schedules.enable", Verb.MANAGE, Effect.ADMIN),
        _op("schedules.disable", Verb.MANAGE, Effect.ADMIN),
        _op("schedules.run_now", Verb.ACT, Effect.WRITE),
        _op("proposals.list", Verb.ASK, Effect.READ),
        _op("proposals.get", Verb.ASK, Effect.READ),
        _op("proposals.review", Verb.MANAGE, Effect.ADMIN),
    )
