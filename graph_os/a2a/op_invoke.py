"""A2A projection of the shared GraphOS operation invocation pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import A2AMessage

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


class _TaskConfirmParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^a2a-[0-9a-f]{64}$")
    work_item_version: int = Field(ge=1)
    call_id: str = Field(min_length=1, max_length=512)
    plan_ref: str = Field(min_length=1, max_length=512)
    op: str = Field(min_length=1, max_length=512)
    params_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "deny"]
    message: A2AMessage
    idempotency_key: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class OperationReply:
    """Transport-neutral outcome; application.py renders JSON-RPC."""

    value: Any = None
    code: str = "OK"
    details: Mapping[str, Any] | None = None
    refused: bool = False


CallerResolver = Callable[[], Any]
InvokeFunction = Callable[..., Awaitable[Any]]
CardDiscovery = Callable[[Any], Awaitable[Iterable[str]]]


def verified_a2a_caller() -> Any:
    """Project only the middleware's verified ambient authority into invoke."""

    from agent_utilities.api.session import resolve_session

    from graph_os.api.invoke import VerifiedCaller

    session = resolve_session()
    return VerifiedCaller.from_session(session, request_id=session.trace_context or "")


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

    async def invoke(
        self, method: str, raw: Mapping[str, Any], *, task_service: Any = None
    ) -> OperationReply:
        if method == OP_INVOKE:
            parsed = _InvokeParams.model_validate(raw)
        elif method == PLAN_CONFIRM:
            parsed = (
                _TaskConfirmParams.model_validate(raw)
                if "task_id" in raw
                else _ConfirmParams.model_validate(raw)
            )
        else:
            raise ValueError("Unknown operation method")

        from graph_os.api.registry import Surface

        invoke_fn = self._invoke_fn
        if invoke_fn is None:
            from graph_os.api.invoke import invoke as invoke_fn

        caller = self._caller()
        if method == PLAN_CONFIRM and (
            caller.principal_kind != "human" or caller.delegated
        ):
            return OperationReply(code="FORBIDDEN", refused=True)

        if isinstance(parsed, _TaskConfirmParams):
            if task_service is None or getattr(caller, "session", None) is None:
                return OperationReply(code="UNAVAILABLE", refused=True)
            binding = {
                "task_id": parsed.task_id,
                "work_item_version": parsed.work_item_version,
                "call_id": parsed.call_id,
                "plan_ref": parsed.plan_ref,
                "op": parsed.op,
                "params_digest": parsed.params_digest,
            }
            if (
                len(parsed.message.parts) != 1
                or parsed.message.parts[0].text != parsed.decision
                or parsed.message.metadata.get("graphOsApproval") != binding
            ):
                return OperationReply(code="INVALID_ARGUMENT", refused=True)
            from agent_utilities.api import PendingInputAnswerRequest

            receipt = await task_service.answer_task_approval(
                parsed.task_id,
                PendingInputAnswerRequest(
                    work_item_id=f"workitem:orchestrator:{parsed.task_id}",
                    work_item_version=parsed.work_item_version,
                    call_id=parsed.call_id,
                    plan_ref=parsed.plan_ref,
                    op=parsed.op,
                    params_digest=parsed.params_digest,
                    decision=parsed.decision,
                    idempotency_key=parsed.idempotency_key,
                ),
            )
            return OperationReply(
                value={"accepted": receipt.accepted, "call_id": receipt.call_id}
            )

        result = await invoke_fn(
            parsed.op,
            parsed.params,
            caller,
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
            return OperationReply(code=code, details=details, refused=True)
        return OperationReply(value=result.value)
