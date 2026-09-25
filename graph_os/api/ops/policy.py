"""Model-policy release pointer operations over the existing CAS service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    OpSpec,
)


class ReleaseStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: str = Field(min_length=1, max_length=256)
    channel: Literal["canary", "stable"]


class ReleaseMoveParams(ReleaseStatusParams):
    capability_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    expected_version_id: str | None = None
    next_version_id: str = Field(min_length=1)
    evaluation_id: str | None = None
    change_ref: str = Field(min_length=1)


class ReleaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pointer: dict[str, Any] | None


async def handle_release(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Reuse the existing promotion checks and EG-backed compare-and-swap."""
    from graph_os.control_plane.policy_evolution import (
        EgReleasePointerRepository,
        ModelPolicyReleaseService,
        PolicyEvolutionControlError,
        PolicyReleaseMutation,
    )

    if op.id not in {
        "policy.release.status",
        "policy.release.promote",
        "policy.release.rollback",
    }:
        raise PolicyEvolutionControlError("POLICY_RELEASE_OP_UNKNOWN")
    records = getattr(context.client, "policy_evolution", None)
    if records is None:
        raise PolicyEvolutionControlError("POLICY_EVOLUTION_UNAVAILABLE")
    service = ModelPolicyReleaseService(
        records, EgReleasePointerRepository(context.client)
    )
    caller = context.caller
    if op.id == "policy.release.status":
        request = ReleaseStatusParams.model_validate(params)
        pointer = await service.current(caller.tenant, request.family, request.channel)
    else:
        request = ReleaseMoveParams.model_validate(params)
        mutation = PolicyReleaseMutation(
            tenant_id=caller.tenant,
            family=request.family,
            channel=request.channel,
            operation="rollback" if op.id == "policy.release.rollback" else "promote",
            capability_id=request.capability_id,
            expected_revision=request.expected_revision,
            expected_version_id=request.expected_version_id,
            next_version_id=request.next_version_id,
            evaluation_id=request.evaluation_id,
            granted_scopes=frozenset(caller.effective_scopes),
            change_ref=request.change_ref,
        )
        pointer = await service.apply(mutation)
    return {"pointer": None if pointer is None else pointer.model_dump(mode="json")}


def specs() -> tuple[OpSpec, ...]:
    """No wire ops until the exact scope and EG CAS contract are confirmed."""
    return ()
