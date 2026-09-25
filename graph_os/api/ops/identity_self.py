"""Self-service identity operations shared by MCP, HTTP, A2A and console."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .identity_admin import Result


class ChangePassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str = Field(min_length=1)
    new: str = Field(min_length=8)


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

    return (
        OpSpec(
            id="identity.self.password.change",
            verb=Verb.MANAGE,
            summary="Change the signed-in person's local password",
            examples=("change my local password",),
            params=ChangePassword,
            result=Result,
            binding=Composite(
                handler="graph_os.identity.admin_service.execute_identity_op"
            ),
            executor=Executor.CALLER,
            scopes=frozenset({"identity:self"}),
            effect=Effect.WRITE,
            principals=PrincipalRule.HUMAN,
            confirm=Confirm.NONE,
            surfaces=frozenset(
                {Surface.MCP, Surface.HTTP, Surface.A2A, Surface.CONSOLE}
            ),
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.IDENTITY_CHAIN,
        ),
    )
