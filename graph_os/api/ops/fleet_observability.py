"""Caller-bound, tenant-evidenced reads of the retained fleet event stream."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import msgpack
from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import Composite, Effect, OpSpec, Surface, Verb

_STREAM = "fleet.events"
_PAGE = 1_000
_SCAN_MAX = 100_000  # Matches the webhook's declared retention count.
_FIELDS = (
    "event_id",
    "subject",
    "received_at",
    "correlation_id",
    "actor_id",
    "status",
    "severity",
    "source_type",
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FleetTraceParams(_Params):
    correlation_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=100, ge=1, le=500)


class FleetTouchedParams(_Params):
    resource: str = Field(min_length=1, max_length=1_024)
    limit: int = Field(default=100, ge=1, le=500)


class FleetEventRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str | None = None
    subject: str
    received_at: str | None = None
    correlation_id: str | None = None
    actor_id: str | None = None
    status: str | None = None
    severity: str | None = None
    source_type: str | None = None


class FleetTraceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: str
    events: list[FleetEventRow]


class FleetTouchedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str
    events: list[FleetEventRow]
    actors: list[str]


def _owned_event(raw: bytes, tenant: str) -> Mapping[str, Any] | None:
    try:
        event = msgpack.unpackb(raw, raw=False)
    except (ValueError, TypeError, msgpack.ExtraData, msgpack.FormatError):
        return None
    if not isinstance(event, dict) or event.get("tenant_id") != tenant:
        return None
    # A missing or ambiguous subject cannot support an exact resource lookup.
    if not isinstance(event.get("subject"), str) or not event["subject"]:
        return None
    return event


def _project(event: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        field: value if isinstance(value := event.get(field), str) else None
        for field in _FIELDS
    }


async def _read_events(client: Any, tenant: str) -> list[Mapping[str, Any]]:
    """Bound the full retained scan; never return an incomplete answer as complete."""
    events: list[Mapping[str, Any]] = []
    offset = 0
    scanned = 0
    while scanned < _SCAN_MAX:
        batch = await client.broker.stream_read(
            _STREAM, from_offset=offset, max=min(_PAGE, _SCAN_MAX - scanned)
        )
        if not batch:
            return events
        for message_offset, payload in batch:
            if not isinstance(message_offset, int) or message_offset < offset:
                raise RuntimeError("fleet stream returned invalid offsets")
            event = _owned_event(payload, tenant)
            if event is not None:
                events.append(event)
        scanned += len(batch)
        offset = batch[-1][0] + 1
    # The retention window may grow during a scan. Refuse to claim completeness.
    if await client.broker.stream_read(_STREAM, from_offset=offset, max=1):
        raise RuntimeError("fleet event scan exceeded its bound")
    return events


async def handle_fleet_observability(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Use only the verified caller's tenant/client, never a process identity."""
    tenant = context.caller.tenant
    if not isinstance(tenant, str) or not tenant or context.service_identity:
        raise PermissionError("caller-bound fleet read required")
    if op.id == "fleet.trace":
        request = FleetTraceParams.model_validate(params)
        events = await _read_events(context.client, tenant)
        matching = [
            event
            for event in events
            if event.get("correlation_id") == request.correlation_id
        ]
        return {
            "correlation_id": request.correlation_id,
            "events": [_project(event) for event in matching[: request.limit]],
        }
    if op.id == "fleet.touched":
        request = FleetTouchedParams.model_validate(params)
        events = await _read_events(context.client, tenant)
        matching = [
            event for event in events if event.get("subject") == request.resource
        ]
        matching.sort(
            key=lambda event: str(event.get("received_at") or ""), reverse=True
        )
        selected = matching[: request.limit]
        return {
            "resource": request.resource,
            "events": [_project(event) for event in selected],
            "actors": sorted(
                {
                    str(event["actor_id"])
                    for event in selected
                    if isinstance(event.get("actor_id"), str) and event["actor_id"]
                }
            ),
        }
    raise ValueError("unknown fleet observability operation")


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="fleet.trace",
            verb=Verb.ASK,
            summary="Find tenant-evidenced fleet events for one correlation",
            examples=("Show fleet events for this correlation",),
            params=FleetTraceParams,
            result=FleetTraceResult,
            binding=Composite(
                handler="graph_os.api.ops.fleet_observability.handle_fleet_observability"
            ),
            scopes=frozenset({"fleet:read"}),
            effect=Effect.READ,
            surfaces=frozenset({Surface.HTTP, Surface.A2A}),
        ),
        OpSpec(
            id="fleet.touched",
            verb=Verb.ASK,
            summary="Find tenant-evidenced fleet events that touched a resource",
            examples=("Show fleet actors that touched this resource",),
            params=FleetTouchedParams,
            result=FleetTouchedResult,
            binding=Composite(
                handler="graph_os.api.ops.fleet_observability.handle_fleet_observability"
            ),
            scopes=frozenset({"fleet:read"}),
            effect=Effect.READ,
            surfaces=frozenset({Surface.HTTP, Surface.A2A}),
        ),
    )
