"""Decide operations: service-executed commit (GRAPHOS-OPS-R021.1).

The commit path runs only under the service executor so no caller can
materialize an agent decision using only a human-scoped token; the runner
itself stays a typed seam (``agent_utilities.decide``) composed by the
serving root, matching the ingest/runner-facade convention in
``graph_os/api/ops`` (see GRAPHOS-OPS-R020).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Executor,
    OpSpec,
    PrincipalRule,
    SubjectRef,
    SubjectSource,
    Verb,
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecideCommitParams(_Params):
    request: dict[str, Any] = Field(min_length=0)


class DecideCommitResult(_Params):
    value: dict[str, Any]


async def handle_decide_commit(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Commit a decision through the service-bound decide runner only."""
    runner = context.services.get("decide_runner")
    if runner is None:
        raise RuntimeError("decide runner is not composed")
    result = await runner.commit(dict(params["request"]), tenant=context.caller.tenant)
    return {"value": dict(result)}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="decide.commit",
            verb=Verb.ACT,
            summary="Commit an agent decision through the service executor",
            examples=("commit this routing decision",),
            params=DecideCommitParams,
            result=DecideCommitResult,
            binding=Composite(handler="graph_os.api.ops.decide.handle_decide_commit"),
            scopes=frozenset({"decide:commit"}),
            executor_scopes=frozenset({"decide:commit"}),
            effect=Effect.WRITE,
            executor=Executor.SERVICE,
            principals=PrincipalRule.SERVICE_ONLY,
            subject=SubjectRef(source=SubjectSource.CALLER_TENANT),
            audit=AuditClass.EVENT,
        ),
    )


specs = operations

__all__ = [
    "DecideCommitParams",
    "DecideCommitResult",
    "handle_decide_commit",
    "operations",
    "specs",
]
