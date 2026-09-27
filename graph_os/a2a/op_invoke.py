"""A2A projection of the shared GraphOS operation invocation pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
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


CallerResolver = Callable[[], Any]
InvokeFunction = Callable[..., Awaitable[Any]]


def _reply_from_result(result: Any) -> OperationReply:
    """Map the shared operation outcome to an A2A response."""
    code = result.code
    details = result.details
    if code in {"CONFIRMATION_REQUIRED", "STEP_UP_REQUIRED"}:
        # The caller must answer with graphos.plan/confirm or visit the
        # human console. A2A never performs a console confirmation.
        return OperationReply(
            value={
                "state": "input-required",
                "code": code,
                "plan_ref": details["plan_ref"],
                "preview": dict(details),
            },
            code=code,
        )
    if code != "OK":
        return OperationReply(code=code, details=details, refused=True)
    return OperationReply(value=result.value)


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
    ) -> None:
        self._services = services
        self._caller = caller
        self._invoke_fn = invoke_fn

    async def invoke(self, method: str, raw: Mapping[str, Any]) -> OperationReply:
        parsed: _InvokeParams | _ConfirmParams
        if method == OP_INVOKE:
            parsed = _InvokeParams.model_validate(raw)
        elif method == PLAN_CONFIRM:
            parsed = _ConfirmParams.model_validate(raw)
        else:
            raise ValueError("Unknown operation method")

        from graph_os.api.registry import Surface

        invoke_fn = self._invoke_fn
        if invoke_fn is None:
            from graph_os.api.invoke import invoke

            invoke_fn = invoke

        result = await invoke_fn(
            parsed.op,
            parsed.params,
            self._caller(),
            Surface.A2A,
            services=self._services,
            plan_ref=parsed.plan_ref,
            idempotency_key=parsed.idempotency_key,
        )
        return _reply_from_result(result)
