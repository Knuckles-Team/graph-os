"""Tenant-bound approval queue intents (MCPI-31)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalListParams(_Params):
    limit: int = Field(default=50, ge=1, le=200)


class ApprovalGetParams(_Params):
    approval_id: str = Field(pattern=r"^action_approval:[A-Za-z0-9_-]{1,128}$")


class ApprovalDecisionParams(ApprovalGetParams):
    expected_revision: int = Field(ge=1)


class ApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: object


_HANDLER = Composite(handler="graph_os.access.approvals.execute")
_SURFACES = frozenset({Surface.HTTP, Surface.CONSOLE})


def specs() -> tuple[OpSpec, ...]:
    """Separate queue reads from human, console-confirmed decisions."""
    reads = tuple(
        OpSpec(
            id=f"approvals.{name}",
            verb=Verb.ASK,
            summary=f"{name.capitalize()} caller-tenant action approvals.",
            examples=(f"{name.capitalize()} pending action approvals",),
            params=params,
            result=ApprovalResult,
            binding=_HANDLER,
            scopes=frozenset({"approvals:read"}),
            surfaces=_SURFACES,
            idempotency=Idempotency.NATURAL,
        )
        for name, params in (("list", ApprovalListParams), ("get", ApprovalGetParams))
    )
    decisions = tuple(
        OpSpec(
            id=f"approvals.{name}",
            verb=Verb.MANAGE,
            summary=f"{name.capitalize()} one pending action approval at the console.",
            examples=(f"{name.capitalize()} this action approval",),
            params=ApprovalDecisionParams,
            result=ApprovalResult,
            binding=_HANDLER,
            scopes=frozenset({"approvals:decide"}),
            effect=Effect.ADMIN,
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            confirm=Confirm.CONSOLE,
            surfaces=_SURFACES,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        )
        for name in ("grant", "deny")
    )
    return (*reads, *decisions)
