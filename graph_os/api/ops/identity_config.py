"""Identity policy, mode and audit operations backed by Method::Identity."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from .identity_admin import Collection


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(StrictModel):
    pass


class PolicySet(StrictModel):
    expected_epoch: int = Field(ge=0)
    registration_policy: Literal["open", "invite", "admin_only", "disabled"] | None = (
        None
    )
    local_fallback: Literal["off", "break_glass", "full"] | None = None
    password_min_chars: int | None = Field(default=None, ge=8, le=256)


class AuditPage(StrictModel):
    after: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class ModeTransition(StrictModel):
    to: Literal["none", "local", "external"]
    ack: str | None = None
    local_fallback: Literal["off", "break_glass", "full"] | None = None


class ConfigValue(RootModel[dict[str, Any]]):
    pass


class AuditVerification(StrictModel):
    valid: bool
    first_broken_seq: int | None = None


_HANDLER = "graph_os.identity.admin_service.execute_identity_op"


def specs() -> tuple[Any, ...]:
    """Config ops, with mode transition routed through the issuer broker."""
    from graph_os.api.registry import (
        AuditClass,
        Composite,
        Confirm,
        Effect,
        Executor,
        Idempotency,
        OpSpec,
        PrincipalRule,
        Surface,
        Verb,
    )

    all_surfaces = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A})
    definitions: tuple[tuple[str, type[BaseModel], type[BaseModel], bool], ...] = (
        ("identity.policy.get", Empty, ConfigValue, True),
        ("identity.policy.set", PolicySet, ConfigValue, False),
        ("identity.mode.status", Empty, ConfigValue, True),
        ("identity.mode.transition", ModeTransition, ConfigValue, False),
        ("identity.audit.list", AuditPage, Collection, True),
        ("identity.audit.export", AuditPage, Collection, True),
        ("identity.audit.verify", Empty, AuditVerification, True),
    )
    return tuple(
        OpSpec(
            id=op_id,
            verb=Verb.ASK if read else Verb.MANAGE,
            summary=op_id.replace("identity.", "Inspect identity ").replace(".", " "),
            examples=(op_id.replace(".", " "),),
            params=params,
            result=result,
            binding=Composite(handler=_HANDLER),
            executor=Executor.CALLER,
            scopes=frozenset({"identity:read" if read else "identity:admin"}),
            effect=Effect.READ if read else Effect.ADMIN,
            principals=PrincipalRule.ANY if read else PrincipalRule.HUMAN_UNDELEGATED,
            confirm=Confirm.NONE if read else Confirm.CONSOLE,
            surfaces=all_surfaces,
            idempotency=Idempotency.NONE if read else Idempotency.KEY_REQUIRED,
            audit=AuditClass.NONE if read else AuditClass.IDENTITY_CHAIN,
        )
        for op_id, params, result, read in definitions
    )
