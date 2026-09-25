"""Attended browser-control operations over the existing WebUI authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)


class BrowserParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]


class BrowserResult(BaseModel):
    value: dict[str, Any]


async def handle_browser(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Delegate to the WebUI-owned browser authority with its session checks."""
    from agent_utilities.api import use_session

    if context.caller.session is None:
        raise RuntimeError("browser operation requires a verified session")
    dispatch_browser_control = context.services.get("browser_control")
    if dispatch_browser_control is None:
        raise RuntimeError("browser-control authority is not bound")
    action = op.id.removeprefix("browser.")
    with use_session(context.caller.session):
        receipt = await dispatch_browser_control(
            action, dict(params["payload"]), context.caller
        )
    return {"value": receipt.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    handler = Composite(handler="graph_os.api.ops.browser.handle_browser")
    return tuple(
        OpSpec(
            id=f"browser.{action}",
            verb=Verb.ACT,
            summary=f"{action.replace('_', ' ').capitalize()} an attended browser call",
            examples=(f"{action.replace('_', ' ')} for my attended browser",),
            params=BrowserParams,
            result=BrowserResult,
            binding=handler,
            scopes=frozenset({"kg:write"}),
            effect=Effect.WRITE,
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            surfaces=frozenset({Surface.HTTP, Surface.CONSOLE}),
            audit=AuditClass.EVENT,
        )
        for action in (
            "issue_lease",
            "execute_call",
            "cancel_call",
            "reconcile_call",
            "revoke_lease",
        )
    )
