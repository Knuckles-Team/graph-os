"""Decision reads and guarded statistical decision jobs."""

from __future__ import annotations

from graph_os.api.registry import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Verb,
)


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
    )
