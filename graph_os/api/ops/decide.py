"""Decision reads and guarded statistical decision jobs."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Verb,
)


class _ReceiptParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReceiptParams(_ReceiptParams):
    receipt_digest: str = Field(pattern=r"^sha256:[0-9a-fA-F]{64}$")


class ReceiptPageParams(_ReceiptParams):
    after: str | None = Field(default=None, pattern=r"^sha256:[0-9a-fA-F]{64}$")
    limit: int = Field(default=20, ge=1, le=50)


async def _receipt_read(context: Any, variant: str, request: Mapping[str, Any]) -> Any:
    from epistemic_graph.generated.coordination import send_decision_eval

    result = await send_decision_eval(
        context.client._client,
        {"op": {"op": variant, "request": {"tenant_id": context.caller.tenant, **request}}},
    )
    return result.payload


async def receipt_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = ReceiptParams.model_validate(params)
    return await _receipt_read(context, "receipt", request.model_dump())


async def receipts_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = ReceiptPageParams.model_validate(params)
    return await _receipt_read(context, "receipts", request.model_dump())


def _method(name: str, result_domain: str) -> tuple[EgSchemaRef, EgSchemaRef, EgMethod]:
    return (
        EgSchemaRef(path=f"contract/schemas/method.request.json#/methods/{name}"),
        EgSchemaRef(
            path=f"contract/schemas/result.{result_domain}.json#/methods/{name}"
        ),
        EgMethod(service=name, op=name),
    )


def specs() -> tuple[OpSpec, ...]:
    decide_params, decide_result, decide_binding = _method("Decide", "query")
    assemble_params, assemble_result, assemble_binding = _method(
        "AgentAssemble", "storage"
    )
    commit_params, commit_result, commit_binding = _method("DecisionCommit", "storage")
    fit_params, fit_result, fit_binding = _method("DecisionFit", "coordination")
    eval_params, eval_result, eval_binding = _method("DecisionEval", "coordination")
    return (
        OpSpec(
            id="decide.evaluate",
            verb=Verb.ASK,
            summary="Evaluate a statistical question against caller-visible candidates.",
            examples=("Rank these candidates using the pinned decision policy",),
            params=decide_params,
            result=decide_result,
            binding=decide_binding,
            scopes=frozenset({"query:decide"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decide.assemble",
            verb=Verb.ASK,
            summary="Prove an agent graph against a tenant-bound library snapshot.",
            examples=("Assemble agents that satisfy this task",),
            params=assemble_params,
            result=assemble_result,
            binding=assemble_binding,
            scopes=frozenset({"agent:assemble-read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decide.commit",
            verb=Verb.MANAGE,
            summary="Commit a verified assembly decision using a service mutation context.",
            examples=("Commit this assembly decision record",),
            params=commit_params,
            result=commit_result,
            binding=commit_binding,
            scopes=frozenset({"agent:decision-write"}),
            effect=Effect.WRITE,
            principals=PrincipalRule.SERVICE_ONLY,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="decide.fit",
            verb=Verb.MANAGE,
            summary="Submit or inspect a bounded decision-fit job.",
            examples=("Fit a draft decision head on labelled data",),
            params=fit_params,
            result=fit_result,
            binding=fit_binding,
            scopes=frozenset({"admin:decision-fit"}),
            effect=Effect.ADMIN,
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="decide.eval.replay",
            verb=Verb.MANAGE,
            summary="Submit or inspect a decision evaluation and replay job.",
            examples=("Evaluate this draft head against held-out outcomes",),
            params=eval_params,
            result=eval_result,
            binding=eval_binding,
            scopes=frozenset({"admin:decision-eval"}),
            effect=Effect.ADMIN,
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="decide.eval.receipt",
            verb=Verb.ASK,
            summary="Read one tenant-bound decision evaluation receipt by digest.",
            examples=("Show the calibrated evaluation receipt",),
            params=ReceiptParams,
            result=eval_result,
            binding=Composite(handler="graph_os.api.ops.decide.receipt_handler"),
            scopes=frozenset({"admin:decision-eval"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decide.eval.receipts",
            verb=Verb.ASK,
            summary="List a bounded page of tenant-bound evaluation receipts.",
            examples=("Show evaluation receipts for the calibration dashboard",),
            params=ReceiptPageParams,
            result=eval_result,
            binding=Composite(handler="graph_os.api.ops.decide.receipts_handler"),
            scopes=frozenset({"admin:decision-eval"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
