"""A2A projection of the shared GraphOS operation invocation pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

OP_INVOKE = "graphos.op/invoke"
PLAN_CONFIRM = "graphos.plan/confirm"
OPERATION_METHODS = frozenset({OP_INVOKE, PLAN_CONFIRM})


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str = Field(min_length=1, max_length=256)
    params: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=512)


class _InvokeParams(_Params):
    plan_ref: str | None = Field(default=None, min_length=1, max_length=512)


class _ConfirmParams(_Params):
    plan_ref: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class OperationReply:
    """Transport-neutral outcome; application.py renders JSON-RPC."""

    value: Any = None
    code: str = "OK"
    details: Mapping[str, Any] | None = None
    refused: bool = False
    source: str = "graphos"


CallerResolver = Callable[[], Any]
InvokeFunction = Callable[..., Awaitable[Any]]
CardDiscovery = Callable[[Any], Awaitable[Iterable[str]]]


def verified_a2a_caller() -> Any:
    """Project only the middleware's verified ambient authority into invoke."""

    from agent_utilities.api.session import resolve_session

    from graph_os.api.invoke import VerifiedCaller

    session = resolve_session()
    actor = session.actor
    actor_type = str(actor.actor_type)
    return VerifiedCaller(
        principal=str(actor.actor_id),
        tenant=session.tenant,
        effective_scopes=frozenset(session.scopes),
        engine_claims={"principal": str(actor.actor_id), "tenant": session.tenant},
        principal_kind="human" if actor_type == "human" else "service",
        delegated=actor_type == "ai_agent",
        policy_revision=str(session.policy_version),
        request_id=session.trace_context or "",
        session=session,
    )


class OperationProjection:
    """Projection dependencies are supplied by the GraphOS composition root."""

    def __init__(
        self,
        services: Any,
        *,
        caller: CallerResolver = verified_a2a_caller,
        invoke_fn: InvokeFunction | None = None,
        card_discovery: CardDiscovery | None = None,
    ) -> None:
        self._services = services
        self._caller = caller
        self._invoke_fn = invoke_fn
        self._card_discovery = card_discovery

    async def visible_card_ops(self) -> tuple[Any, ...]:
        """Narrow a trusted discovery result by A2A surface and caller authority."""
        if self._card_discovery is None:
            return ()
        from graph_os.api.registry import Surface

        caller = self._caller()
        try:
            selected = frozenset(await self._card_discovery(caller))
        except Exception:
            return ()
        if not selected:
            return ()
        return self._services.registry.find(
            caller,
            surface=Surface.A2A,
            policy=lambda op, _: op.id in selected,
        )

    async def invoke(self, method: str, raw: Mapping[str, Any]) -> OperationReply:
        if method == OP_INVOKE:
            parsed = _InvokeParams.model_validate(raw)
        elif method == PLAN_CONFIRM:
            parsed = _ConfirmParams.model_validate(raw)
        else:
            raise ValueError("Unknown operation method")

        from graph_os.api.registry import Surface

        invoke_fn = self._invoke_fn
        if invoke_fn is None:
            from graph_os.api.invoke import invoke as invoke_fn

        result = await invoke_fn(
            parsed.op,
            parsed.params,
            self._caller(),
            Surface.A2A,
            services=self._services,
            plan_ref=parsed.plan_ref,
            idempotency_key=parsed.idempotency_key,
        )
        code = result.code
        details = result.details
        if code in {"CONFIRMATION_REQUIRED", "STEP_UP_REQUIRED"}:
            # Only a PLAN confirmation can return to this authenticated A2A
            # caller. The EG lease independently binds these exact parameters
            # and will reject a changed op, actor, tenant, or policy revision.
            # A CONSOLE confirmation always stays with the human console.
            plan_ref = details.get("plan_ref") if details else None
            if not isinstance(plan_ref, str) or not plan_ref:
                return OperationReply(code="UNAVAILABLE", refused=True)
            metadata: dict[str, Any] = {}
            if code == "CONFIRMATION_REQUIRED":
                metadata["graphOsPlan"] = {
                    "plan_ref": plan_ref,
                    "op": parsed.op,
                    "params": parsed.params,
                    "confirm": "plan",
                }
            elif details.get("console_url") == f"/console/confirm/{plan_ref}":
                metadata["graphOsConsoleUrl"] = details["console_url"]
            return OperationReply(
                value={
                    "state": "input-required",
                    "code": code,
                    "plan_ref": plan_ref,
                    "preview": dict(details),
                    "status": {
                        "state": "input-required",
                        "message": {
                            "role": "agent",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": (
                                        "Open the console to confirm"
                                        if code == "STEP_UP_REQUIRED"
                                        else "Confirmation required"
                                    ),
                                }
                            ],
                            "messageId": plan_ref,
                            "metadata": metadata,
                        },
                    },
                },
                code=code,
            )
        if code != "OK":
            return OperationReply(
                code=code,
                details=details,
                refused=True,
                source=getattr(result, "source", "graphos"),
            )
        return OperationReply(value=result.value)
