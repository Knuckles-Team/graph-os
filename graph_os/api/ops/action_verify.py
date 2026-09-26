"""Read-only, human-bound fleet action policy preview (MCPI-31)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from graph_os.api.registry import (
    Composite,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)


class ActionVerifyParams(BaseModel):
    """Caller supplies an intent, never its actor or ActionPolicy source role."""

    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    target: str = Field(min_length=1, max_length=512)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2_048)

    @model_validator(mode="after")
    def _bounded_payload(self) -> ActionVerifyParams:
        if (
            len(self.params) > 64
            or len(
                json.dumps(
                    self.params, ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            > 16_384
        ):
            raise ValueError("action parameters exceed the preview bound")
        return self


class ActionVerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: dict[str, Any]


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="fleet.actions.verify",
            verb=Verb.ASK,
            summary="Preview a human operator's action against the bound policy.",
            examples=("Check whether this service action would require approval",),
            params=ActionVerifyParams,
            result=ActionVerifyResult,
            binding=Composite(handler="graph_os.api.ops.action_verify.execute"),
            scopes=frozenset({"fleet:read"}),
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            surfaces=frozenset({Surface.HTTP, Surface.CONSOLE}),
            idempotency=Idempotency.NATURAL,
        ),
    )


async def execute(context: Any, params: dict[str, Any], op: OpSpec) -> dict[str, Any]:
    """Pass only server-bound identity to an injected preview authority."""
    from graph_os.api.invoke.pipeline import OperationRefused

    if op.id != "fleet.actions.verify":
        raise OperationRefused("UNKNOWN_OP")
    caller = context.caller
    if caller.principal_kind != "human" or caller.delegated:
        raise OperationRefused("PRINCIPAL_NOT_ALLOWED")
    authority = context.services.get("action_verify")
    if authority is None:
        raise OperationRefused("UNAVAILABLE")
    verifier = getattr(authority, "verify", None)
    if verifier is None:
        raise OperationRefused("UNAVAILABLE")
    verdict = await verifier(
        client=context.client,
        tenant=caller.tenant,
        actor_id=caller.principal,
        source="manual",
        kind=params["kind"],
        target=params["target"],
        params=params["params"],
        reason=params["reason"],
    )
    if not isinstance(verdict, dict):
        raise OperationRefused("UNAVAILABLE")
    decision = verdict.get("decision")
    tier = verdict.get("tier")
    reason = verdict.get("reason")
    invariant = verdict.get("invariant", "")
    if (
        not isinstance(decision, str)
        or decision
        not in {"allow", "allow_notify", "queue_approval", "deny", "unavailable"}
        or not isinstance(tier, str)
        or tier not in {"auto", "auto_notify", "approval_required", "forbidden"}
        or not isinstance(reason, str)
        or len(reason) > 2_048
        or not isinstance(invariant, str)
        or len(invariant) > 128
    ):
        raise OperationRefused("UNAVAILABLE")
    return {
        "value": {
            "decision": decision,
            "tier": tier,
            "reason": reason,
            "invariant": invariant,
            "allowed": False,
        }
    }
