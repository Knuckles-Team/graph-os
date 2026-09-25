"""GraphOS bundled-skill runtime execution logic."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any

from agent_utilities.orchestration.run_identity import is_run_id, new_run_id
from agent_utilities.security.persistence_privacy import persistence_reference

from .runtime_validation_architecture import (
    _capture_architecture_operations,
)
from .runtime_validation_authority import (
    _bounded_sync_call,
    _expected_trace_name,
    _trace_snapshot,
    _verify_parent_ingested_trace,
    _wait_for_expected_trace,
)
from .runtime_validation_core import (
    _DIRECT_CASE_LOCK,
    _DIRECT_MAX_OUTPUT_TOKENS,
    _FAIL,
    _PASS,
    _SYNC_CALL_POISONED,
    CaseResult,
    DelegationContractError,
    SemanticOutput,
    ValidationCase,
)
from .runtime_validation_matrix import (
    _call_tool,
    _contract_instruction,
    _direct_evidence_authority,
    _direct_execution_prompt,
    _direct_semantic_output_type,
    _extract_delegation_envelope,
    _semantic_from_delegation_output,
    _skill_instruction_digest,
    _skill_runtime_body,
    _usage_counts,
    validate_semantic_output,
)


def _opaque_ref(kind: str, value: str) -> str:
    return persistence_reference(kind, value, namespace="skill-validation")


def _expected_delegated_model_ref(model_class: str) -> str:
    """Resolve the exact configured model identity for a delegated model class."""

    from agent_utilities.orchestration.agent_runner import (
        _configured_model_for_class,
    )

    selected = _configured_model_for_class(model_class)
    return persistence_reference("model", selected.id, namespace="orchestration-run")


def _validate_delegated_runtime_evidence(
    case: ValidationCase, status: dict[str, Any]
) -> tuple[list[str], str, str, str]:
    """Validate actual trace metadata, never the fixture's requested label alone."""
    errors: list[str] = []
    model_ref = str(status.get("model_ref") or "")
    skill_ref = str(status.get("skill_ref") or "")
    digest = str(status.get("skill_instruction_digest") or "")
    expected_skill_ref = persistence_reference(
        "skill", case.skill, namespace="execution-trace"
    )
    expected_model_ref = _expected_delegated_model_ref(case.model_class)
    if str(status.get("model_class") or "") != case.model_class:
        errors.append("model_class_mismatch")
    if not model_ref:
        errors.append("model_reference_missing")
    elif model_ref != expected_model_ref:
        errors.append("model_reference_mismatch")
    if skill_ref != expected_skill_ref:
        errors.append("skill_reference_mismatch")
    if digest != _skill_instruction_digest(case.skill):
        errors.append("skill_instruction_digest_mismatch")
    return errors, model_ref, skill_ref, digest


def _delegation_terminal_error_code(status: dict[str, Any]) -> str | None:
    """Classify terminal failure metadata without retaining its raw text."""

    state = str(status.get("status") or "").strip().casefold()
    if state == "completed":
        return None
    error_text = str(status.get("error") or "")
    known_types = (
        "ContextCompilationError",
        "PermissionError",
        "SessionRequiredError",
        "ScopeError",
        "TransportSecurityError",
        "ValidationError",
        "TimeoutError",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "ImportError",
        "ConnectionError",
        "HTTPStatusError",
        "ModelHTTPError",
        "UnexpectedModelBehavior",
        "ToolError",
    )
    failure_type = next((name for name in known_types if name in error_text), "")
    if failure_type:
        return f"delegation_terminal_{failure_type.casefold()}"
    normalized_state = re.sub(r"[^a-z0-9_]+", "_", state).strip("_")
    return f"delegation_terminal_{normalized_state or 'failure'}"


def _validation_reasoning_effort(model_class: str, *, delegated: bool) -> str | None:
    """Return the provider-neutral reasoning override for validation.

    Economy validation must not send the OpenAI-compatible ``"none"``
    extension: it is not part of the portable effort vocabulary and some
    otherwise compatible runtimes reject it.  Direct model construction uses
    ``None`` to omit the field; the string-only MCP delegation surface uses an
    empty value, which ``graph_orchestrate`` converts to the same omission.
    Standard delegated cases retain their bounded ``low`` effort.
    """

    if model_class == "economy":
        return "" if delegated else None
    return "low" if delegated else None


async def _attach_trace_evidence(
    result: CaseResult,
    *,
    client: Any,
    langfuse_tool: str,
    started_at: str,
    expected_trace_name: str,
    expected_trace_evidence: dict[str, str],
    trace_timeout: float,
) -> None:
    """Record the exact trace and its parent-ingested node, or a typed error."""

    try:
        trace_id, linkage = await _wait_for_expected_trace(
            client,
            langfuse_tool,
            started_at,
            expected_trace_name,
            expected_trace_evidence,
            trace_timeout,
        )
        result.trace = _PASS
        result.trace_linkage = linkage
        result.trace_name = expected_trace_name
        result.langfuse_match_count = 1
        result.trace_ref = _opaque_ref("trace", trace_id)
        result.parent_kg_readback_count = await _verify_parent_ingested_trace(
            client, expected_trace_name, min(15.0, trace_timeout)
        )
        result.parent_ingestion = _PASS
    except Exception as exc:  # noqa: BLE001 - report only the exception class
        result.add_error(f"trace_or_ingestion_{type(exc).__name__}")


def _direct_case_model(case: ValidationCase, result: CaseResult) -> tuple[Any, str]:
    """Bind the configured model and skill identity onto the case result."""

    from agent_utilities.core.model_factory import create_model
    from agent_utilities.orchestration.agent_runner import (
        _configured_model_for_class,
    )

    selected_model = _configured_model_for_class(case.model_class)
    model = create_model(
        model_id=selected_model.id,
        reasoning_effort=_validation_reasoning_effort(
            case.model_class, delegated=False
        ),
    )
    model_name = str(getattr(model, "model_name", "") or "")
    if not model_name:
        raise RuntimeError("runtime_model_identity_unavailable")
    result.model_ref = _opaque_ref("model", model_name)
    expected_model_ref = _opaque_ref("model", selected_model.id)
    if result.model_ref != expected_model_ref:
        result.add_error("direct_model_selection_mismatch")
    else:
        result.model_selection = _PASS
    instruction_digest = _skill_instruction_digest(case.skill)
    result.skill_ref = persistence_reference(
        "skill", case.skill, namespace="execution-trace"
    )
    result.skill_body_ref = _opaque_ref("skill_body", instruction_digest)
    result.skill_binding = _PASS
    return model, model_name


def _direct_model_settings(
    *, system_prompt: str, model_identity: str, case_timeout: float
) -> Any:
    """Build bounded direct-run settings, folding the provider prompt-cache hint."""

    from pydantic_ai import ModelSettings

    direct_model_settings: Any = ModelSettings(
        # The closed JSON contract is intentionally small. A bounded
        # generation keeps CPU-only local-model validation practical.
        max_tokens=_DIRECT_MAX_OUTPUT_TOKENS,
        temperature=0.0,
        timeout=case_timeout,
    )
    try:
        # D-54c-4 — this call bypasses attach_profile_resolver (it invokes
        # agent.run() directly with an explicit model_settings), so fold the
        # provider-native prompt-cache directive here too (CONCEPT:AU-ORCH.optimization.provider-prompt-cache).
        from agent_utilities.caching.prompt_cache import fold_prompt_cache_hint

        return fold_prompt_cache_hint(
            direct_model_settings,
            system_prompt=system_prompt,
            model_identity=model_identity,
        )
    except Exception:  # noqa: BLE001 - prompt-cache hint is best-effort
        return direct_model_settings


def _record_direct_semantic(case: ValidationCase, result: CaseResult, run: Any) -> None:
    """Validate the closed semantic contract of a direct run into the result."""

    semantic = SemanticOutput.model_validate(run.output)
    semantic_errors = validate_semantic_output(case, semantic)
    result.selected_routes = tuple(sorted(semantic.selected_routes))
    for error in semantic_errors:
        result.add_error(error)
    result.semantic = _PASS if not semantic_errors else _FAIL


async def _export_direct_trace(
    result: CaseResult,
    *,
    run: Any,
    validation_run_id: str,
    model_name: str,
    trace_evidence: dict[str, str],
    case_timeout: float,
) -> None:
    """Emit the exact run trace through the single bounded blocking-SDK slot."""

    from agent_utilities.observability.langfuse_exporter import get_langfuse_exporter

    exporter = get_langfuse_exporter()
    if exporter is None:
        result.add_error("trace_exporter_unavailable")
        return

    def emit_trace() -> bool | None:
        if not exporter.enabled:
            return None
        emitted = exporter.export_graph_run(
            run_id=validation_run_id,
            query="",
            status=("success" if result.semantic == _PASS else "validation_failed"),
            token_usage=_usage_counts(run),
            model=model_name,
            metadata={"validation_kind": "bundled_skill_direct"},
            evidence={
                key: value for key, value in trace_evidence.items() if key != "run_ref"
            },
        )
        exporter.flush()
        return emitted

    emitted = await _bounded_sync_call(emit_trace, min(30.0, case_timeout))
    if emitted is None:
        result.add_error("trace_exporter_unavailable")
    elif not emitted:
        result.add_error("trace_export_failed")


async def _execute_direct_case(
    case: ValidationCase,
    result: CaseResult,
    *,
    validation_run_id: str,
    expected_trace_name: str,
    case_timeout: float,
) -> dict[str, str]:
    """Run one direct in-process case and return its expected trace evidence."""

    expected_trace_evidence: dict[str, str] = {}
    async with _DIRECT_CASE_LOCK:
        try:
            from agent_utilities.core.contextual_model import create_context_agent

            with _direct_evidence_authority(case.skill):
                model, model_name = _direct_case_model(case, result)
                expected_trace_evidence = {
                    "run_ref": expected_trace_name.removeprefix("graph_run:"),
                    "model_ref": result.model_ref,
                    "model_class": case.model_class,
                    "skill_ref": result.skill_ref,
                    "skill_body_ref": result.skill_body_ref,
                }
                direct_system_prompt = (
                    f"{_skill_runtime_body(case.skill)}\n\n"
                    f"{_contract_instruction(case)}"
                )
                agent = create_context_agent(
                    model=model,
                    output_type=_direct_semantic_output_type(case),
                    system_prompt=direct_system_prompt,
                    model_settings=_direct_model_settings(
                        system_prompt=direct_system_prompt,
                        model_identity=model_name,
                        case_timeout=case_timeout,
                    ),
                    retries=2,
                )
                run = await asyncio.wait_for(
                    agent.run(_direct_execution_prompt(case)), timeout=case_timeout
                )
                _record_direct_semantic(case, result, run)
                await _export_direct_trace(
                    result,
                    run=run,
                    validation_run_id=validation_run_id,
                    model_name=model_name,
                    trace_evidence=expected_trace_evidence,
                    case_timeout=case_timeout,
                )
                result.run_ref = expected_trace_name.removeprefix("graph_run:")
        except Exception as exc:  # noqa: BLE001 - report only the exception class
            result.add_error(f"direct_{type(exc).__name__}")
    return expected_trace_evidence


async def _architecture_probe_blocked(
    case: ValidationCase,
    result: CaseResult,
    *,
    client: Any,
    timeout: float,
) -> bool:
    """Run the RF-021 probe; True when the case must stop on its own errors."""

    try:
        await _capture_architecture_operations(
            case,
            result,
            client=client,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - retain only controlled diagnostics
        result.add_error(f"architecture_probe_{type(exc).__name__}")
        return True
    return bool(result.error_codes)


async def _trace_precheck_blocked(
    result: CaseResult,
    *,
    client: Any,
    langfuse_tool: str,
    trace_timeout: float,
    expected_trace_name: str,
) -> bool:
    """True when the pre-run trace probe blocks the case from executing."""

    try:
        existing = await _trace_snapshot(
            client,
            langfuse_tool,
            min(15.0, trace_timeout),
            expected_name=expected_trace_name,
        )
    except Exception as exc:  # noqa: BLE001 - controlled type-only evidence
        result.add_error(f"trace_precheck_{type(exc).__name__}")
        return True
    if existing:
        result.add_error("trace_run_identifier_preexisting")
        return True
    return False


async def _finalize_delegated_trace(
    result: CaseResult,
    *,
    client: Any,
    langfuse_tool: str,
    started_at: str,
    run_id: str,
    expected_trace_name: str,
    expected_trace_evidence: dict[str, str],
    trace_timeout: float,
) -> None:
    """Attach delegated trace evidence, or record why it is unavailable."""

    if not run_id or not expected_trace_name:
        result.add_error("trace_run_identifier_unavailable")
        return
    if not expected_trace_evidence:
        result.add_error("trace_expected_evidence_unavailable")
        return
    await _attach_trace_evidence(
        result,
        client=client,
        langfuse_tool=langfuse_tool,
        started_at=started_at,
        expected_trace_name=expected_trace_name,
        expected_trace_evidence=expected_trace_evidence,
        trace_timeout=trace_timeout,
    )


async def _run_direct_case(
    case: ValidationCase,
    *,
    client: Any,
    langfuse_tool: str,
    tenant_id: str,
    case_timeout: float,
    trace_timeout: float,
) -> CaseResult:
    result = CaseResult(
        case_id=case.case_id,
        skill=case.skill,
        mode=case.mode,
        model_class=case.model_class,
    )
    validation_run_id = new_run_id()
    expected_trace_name = _expected_trace_name(validation_run_id, tenant_id)
    if await _architecture_probe_blocked(
        case, result, client=client, timeout=case_timeout
    ):
        return result
    if await _trace_precheck_blocked(
        result,
        client=client,
        langfuse_tool=langfuse_tool,
        trace_timeout=trace_timeout,
        expected_trace_name=expected_trace_name,
    ):
        return result
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    expected_trace_evidence = await _execute_direct_case(
        case,
        result,
        validation_run_id=validation_run_id,
        expected_trace_name=expected_trace_name,
        case_timeout=case_timeout,
    )
    if _SYNC_CALL_POISONED.is_set():
        return result
    if not expected_trace_evidence:
        result.add_error("trace_expected_evidence_unavailable")
        return result
    await _attach_trace_evidence(
        result,
        client=client,
        langfuse_tool=langfuse_tool,
        started_at=started_at,
        expected_trace_name=expected_trace_name,
        expected_trace_evidence=expected_trace_evidence,
        trace_timeout=trace_timeout,
    )
    return result


def _delegation_request(
    case: ValidationCase, *, max_steps: int, token_budget: int
) -> dict[str, Any]:
    """Build the bounded `graph_orchestrate` request for one delegated case."""

    return {
        "agent_name": case.skill,
        "task": f"{case.task}\n\n{_contract_instruction(case)}",
        "max_steps": max_steps,
        "budget_tokens": token_budget,
        "allowed_tools": ",".join(case.allowed_tools),
        "reasoning_effort": _validation_reasoning_effort(
            case.model_class, delegated=True
        ),
        "model_class": case.model_class,
        "response_format": "json",
    }


def _record_delegated_semantic(
    case: ValidationCase, result: CaseResult, output: Any
) -> None:
    """Validate the delegated semantic contract, retaining only error codes."""

    try:
        semantic = _semantic_from_delegation_output(output)
        semantic_errors = validate_semantic_output(case, semantic)
        result.selected_routes = tuple(sorted(semantic.selected_routes))
        for error in semantic_errors:
            result.add_error(error)
        result.semantic = _PASS if not semantic_errors else _FAIL
    except Exception as exc:  # noqa: BLE001 - controlled semantic evidence only
        if isinstance(exc, DelegationContractError):
            result.add_error(exc.code)
        else:
            result.add_error(f"delegated_semantic_{type(exc).__name__}")


def _delegated_check_status(evidence_errors: list[str], prefix: str) -> str:
    """Pass a delegated check only when no evidence error carries its prefix."""

    return (
        _PASS
        if not any(error.startswith(prefix) for error in evidence_errors)
        else _FAIL
    )


def _record_delegated_evidence(
    case: ValidationCase, result: CaseResult, status: dict[str, Any]
) -> str:
    """Record the delegated run's controlled evidence; return its skill digest."""

    terminal_error = _delegation_terminal_error_code(status)
    if terminal_error:
        result.add_error(terminal_error)
    (
        evidence_errors,
        model_ref,
        skill_ref,
        digest,
    ) = _validate_delegated_runtime_evidence(case, status)
    for error in evidence_errors:
        result.add_error(error)
    result.model_ref = model_ref
    result.skill_ref = skill_ref
    result.skill_body_ref = _opaque_ref("skill_body", digest) if digest else ""
    result.model_selection = _delegated_check_status(evidence_errors, "model_")
    result.skill_binding = _delegated_check_status(evidence_errors, "skill_")
    result.delegation = (
        _PASS if not evidence_errors and terminal_error is None else _FAIL
    )
    return digest


def _delegated_trace_evidence(
    case: ValidationCase, result: CaseResult, expected_trace_name: str, digest: str
) -> dict[str, str]:
    """Return the exact trace evidence, or empty when a reference is missing."""

    if not (result.model_ref and result.skill_ref and digest):
        return {}
    return {
        "run_ref": expected_trace_name.removeprefix("graph_run:"),
        "model_ref": result.model_ref,
        "model_class": case.model_class,
        "skill_ref": result.skill_ref,
        "skill_body_ref": _opaque_ref("skill_body", digest),
    }


async def _record_delegated_run(
    case: ValidationCase,
    result: CaseResult,
    *,
    client: Any,
    run_id: str,
    expected_trace_name: str,
    case_timeout: float,
) -> dict[str, str]:
    """Await the delegated run and record its controlled completion evidence."""

    if not run_id:
        result.add_error("delegation_run_handle_missing")
        return {}
    result.run_ref = expected_trace_name.removeprefix("graph_run:")
    status = await _wait_for_run_completion(client, run_id, min(case_timeout, 30.0))
    digest = _record_delegated_evidence(case, result, status)
    return _delegated_trace_evidence(case, result, expected_trace_name, digest)


async def _run_delegated_case(
    case: ValidationCase,
    *,
    client: Any,
    langfuse_tool: str,
    tenant_id: str,
    max_steps: int,
    token_budget: int,
    case_timeout: float,
    trace_timeout: float,
) -> CaseResult:
    result = CaseResult(
        case_id=case.case_id,
        skill=case.skill,
        mode=case.mode,
        model_class=case.model_class,
        delegation=_FAIL,
    )
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    run_id = ""
    expected_trace_name = ""
    expected_trace_evidence: dict[str, str] = {}
    if await _architecture_probe_blocked(
        case, result, client=client, timeout=case_timeout
    ):
        return result
    try:
        response = await _call_tool(
            client,
            "graph_orchestrate",
            _delegation_request(case, max_steps=max_steps, token_budget=token_budget),
            case_timeout,
        )
        output, run_id = _extract_delegation_envelope(response)
        expected_trace_name = _expected_trace_name(run_id, tenant_id)
        _record_delegated_semantic(case, result, output)
        expected_trace_evidence = await _record_delegated_run(
            case,
            result,
            client=client,
            run_id=run_id,
            expected_trace_name=expected_trace_name,
            case_timeout=case_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - retain only controlled diagnostics
        if isinstance(exc, DelegationContractError):
            result.add_error(exc.code)
        else:
            result.add_error(f"delegated_{type(exc).__name__}")

    await _finalize_delegated_trace(
        result,
        client=client,
        langfuse_tool=langfuse_tool,
        started_at=started_at,
        run_id=run_id,
        expected_trace_name=expected_trace_name,
        expected_trace_evidence=expected_trace_evidence,
        trace_timeout=trace_timeout,
    )
    return result


async def _wait_for_run_completion(
    client: Any, run_id: str, timeout: float
) -> dict[str, Any]:
    """Poll the focused job surface until the delegated run is terminal."""

    if not is_run_id(run_id):
        raise DelegationContractError("delegation_run_id_invalid")

    deadline = time.monotonic() + max(1.0, timeout)
    failed_states = {
        "cancelled",
        "canceled",
        "dead_letter",
        "denied",
        "error",
        "failed",
        "rejected",
    }
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("delegation_status_timeout")
        status = await _call_tool(
            client,
            "graph_jobs",
            {"action": "status", "job_id": run_id},
            min(remaining, 15.0),
        )
        if not isinstance(status, dict):
            raise RuntimeError("delegation_status_not_object")
        state = str(status.get("status") or "").strip().casefold()
        if state == "completed" or state in failed_states or state == "degraded":
            return status
        await asyncio.sleep(min(0.5, max(0.0, remaining)))
