"""Self-service identity operations shared by MCP, HTTP, A2A and console."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel

from .identity_admin import Collection, Page, Result


class ChangePassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str = Field(min_length=1)
    new: str = Field(min_length=8)


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(RootModel[dict[str, Any]]):
    pass


class MfaStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    totp_enrolled: bool
    webauthn_credentials: int
    recovery_codes_left: int


def specs() -> tuple[Any, ...]:
    """Declare only self operations that EG can enforce as the caller."""
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

    definitions: tuple[tuple[str, type[BaseModel], type[BaseModel], bool], ...] = (
        ("identity.self.profile", Empty, Profile, True),
        ("identity.self.sessions.list", Empty, Collection, True),
        ("identity.self.api_keys.list", Page, Collection, True),
        ("identity.self.mfa.status", Empty, MfaStatus, True),
        ("identity.self.password.change", ChangePassword, Result, False),
    )
    return tuple(
        OpSpec(
            id=op_id,
            verb=Verb.ASK if read else Verb.MANAGE,
            summary=op_id.replace("identity.self.", "Inspect own ").replace(".", " "),
            examples=(op_id.replace(".", " "),),
            params=params,
            result=result,
            binding=Composite(
                handler="graph_os.identity.admin_service.execute_identity_op"
            ),
            executor=Executor.CALLER,
            scopes=frozenset({"identity:self"}),
            effect=Effect.READ if read else Effect.WRITE,
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            confirm=Confirm.NONE if read else Confirm.CONSOLE,
            surfaces=frozenset(
                {Surface.MCP, Surface.HTTP, Surface.A2A, Surface.CONSOLE}
            ),
            idempotency=Idempotency.NONE if read else Idempotency.KEY_REQUIRED,
            audit=AuditClass.NONE if read else AuditClass.IDENTITY_CHAIN,
        )
        for op_id, params, result, read in definitions
    )
