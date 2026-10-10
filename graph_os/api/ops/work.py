"""Work-item and offer operations (GRAPHOS-OPS-R022.1).

GRAPHOS-OPS-R022 exposes work-item and offer operations plus evolution loop
operations, with GraphOS's daemon acting only as a status/run/pause facade
while durable state remains owned by the underlying service (see
``specs/hosted-api-operations/spec.md``'s ``work.{offers.*,items.*,status}``
row). This slice (R022.1) proves the work portion -- ``work.items.list``,
``work.items.get``, and ``work.offers.create`` -- through the same
service-bound runner facade ``graph_os.api.ops.decide`` already uses for
``decide.commit``; ``work.status`` and the ``evolution.*`` family are
tracked as GRAPHOS-OPS-R022.2.

Each handler scopes its call to ``context.caller.tenant`` -- the verified
caller identity the shared invoke pipeline already resolved -- so a work
item or offer is only ever visible to its bound principal's tenant; the
runner facade, not this module, holds the durable record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from graph_os.api.ops._common import (
    Params,
    bound_service,
    build_read_op,
    build_write_op,
)
from graph_os.api.registry import OpSpec, Verb


class WorkItemsListParams(Params):
    cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=50, ge=1, le=100)


class WorkItemRefParams(Params):
    item_id: str = Field(min_length=1, max_length=80)


class WorkOfferCreateParams(Params):
    item_id: str = Field(min_length=1, max_length=80)
    terms: dict[str, Any] = Field(default_factory=dict)


class WorkResult(Params):
    value: dict[str, Any]


def _bound_runner(context: Any) -> Any:
    runner = context.services.get("work_runner")
    if runner is None:
        raise RuntimeError("work runner is not bound")
    return runner


async def handle_work(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Dispatch to the service-bound work-item/offer runner; GraphOS owns no state here."""
    runner = _bound_runner(context)
    if op.id == "work.items.list":
        result = await runner.list_items(
            tenant=context.caller.tenant,
            cursor=params.get("cursor"),
            limit=params["limit"],
        )
    elif op.id == "work.items.get":
        result = await runner.get_item(params["item_id"], tenant=context.caller.tenant)
        if result is None:
            raise LookupError("work item not found")
    elif op.id == "work.offers.create":
        if not context.idempotency_key:
            raise ValueError("Idempotency-Key is required")
        result = await runner.create_offer(
            params["item_id"],
            terms=dict(params["terms"]),
            tenant=context.caller.tenant,
            idempotency_key=context.idempotency_key,
        )
    else:
        raise ValueError("unknown work operation")
    return {"value": dict(result)}


class EvolutionLoopStatusParams(Params):
    loop_id: str = Field(min_length=1, max_length=80)


async def handle_evolution_loop_status(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Report one loop's state from the composed evolution runner (GRAPHOS-OPS-R022.2.1)."""
    runner = bound_service(
        context, "evolution_runner", reason="evolution runner is not composed"
    )
    result = await runner.get_loop_status(
        tenant=context.caller.tenant, loop_id=params["loop_id"]
    )
    return {"value": dict(result)}


def operations() -> tuple[OpSpec, ...]:
    handler = "graph_os.api.ops.work.handle_work"
    return (
        build_read_op(
            op_id="work.items.list",
            summary="List this tenant's bound work items",
            examples=("list my work items",),
            params=WorkItemsListParams,
            result=WorkResult,
            handler=handler,
            scope="work:read",
            verb=Verb.FIND,
        ),
        build_read_op(
            op_id="work.items.get",
            summary="Get one owned work item",
            examples=("show this work item",),
            params=WorkItemRefParams,
            result=WorkResult,
            handler=handler,
            scope="work:read",
        ),
        build_write_op(
            op_id="work.offers.create",
            summary="Create an offer against an owned work item",
            examples=("offer to take this work item",),
            params=WorkOfferCreateParams,
            result=WorkResult,
            handler=handler,
            scope="work:write",
        ),
        build_read_op(
            op_id="evolution.loops.status",
            summary="Show one evolution loop's current status",
            examples=("show the status of this evolution loop",),
            params=EvolutionLoopStatusParams,
            result=WorkResult,
            handler="graph_os.api.ops.work.handle_evolution_loop_status",
            scope="work:read",
        ),
    )


specs = operations

__all__ = [
    "EvolutionLoopStatusParams",
    "WorkItemRefParams",
    "WorkItemsListParams",
    "WorkOfferCreateParams",
    "WorkResult",
    "handle_evolution_loop_status",
    "handle_work",
    "operations",
    "specs",
]
