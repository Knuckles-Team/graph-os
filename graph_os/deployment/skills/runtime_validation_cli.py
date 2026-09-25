"""GraphOS bundled-skill runtime cli logic."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_utilities.orchestration.run_identity import new_run_id

from graph_os.deployment.skills.validation import validate as validate_static_suite

from .runtime_validation_authority import (
    _direct_case_minimum_authority_ttl,
    _ensure_tool,
    _expected_trace_name,
    _load_langfuse_tool,
    _renew_delegated_validation_session,
    _renew_direct_validation_session,
    _trace_snapshot,
    _verified_validation_session,
    _verify_langfuse_posture,
)
from .runtime_validation_core import (
    _ARCHITECTURE_SKILL,
    _COMMAND_REFERENCE,
    _DIGEST,
    _RELEASE_ID,
    _SIGNER_COMMAND_REFERENCE,
    _SYNC_CALL_POISONED,
    _VERIFIER_COMMAND_REFERENCE,
    CaseResult,
    ValidationCase,
)
from .runtime_validation_execution import (
    _run_delegated_case,
    _run_direct_case,
)
from .runtime_validation_matrix import (
    load_matrix,
)
from .runtime_validation_report import (
    _validate_result_set,
    publish_report,
    render_report,
)
from .runtime_validation_signing import (
    build_evidence,
    render_evidence,
    sign_and_verify_evidence,
)


def _validated_graph_os_url(args: argparse.Namespace) -> str:
    """Require a configured Graph-OS URL and a metadata-only, ingesting runtime."""

    from agent_utilities.core.config import config, setting

    graph_os_url = str(args.graph_os_url or config.mcp_url or "").strip()
    if not graph_os_url:
        raise RuntimeError("graph_os_url_unconfigured")
    capture_content = str(setting("LANGFUSE_CAPTURE_CONTENT", "false") or "false")
    if capture_content.strip().casefold() in {"1", "true", "yes", "on"}:
        raise RuntimeError("langfuse_content_capture_must_be_disabled")
    if not config.langfuse_kg_auto_ingest:
        raise RuntimeError("langfuse_parent_ingestion_required")
    return graph_os_url


def _required_validation_tools(cases: list[ValidationCase]) -> tuple[str, ...]:
    """Name the exact tool surface this matrix's cases require, in load order."""

    names = ["graph_orchestrate", "graph_query"]
    if any(case.skill == _ARCHITECTURE_SKILL for case in cases):
        names.extend(("graph_search", "graph_code"))
    if any(case.mode == "delegated" for case in cases):
        names.append("graph_jobs")
    return tuple(names)


async def _prepare_validation_tools(
    client: Any, cases: list[ValidationCase], tenant_id: str
) -> str:
    """Load the exact tool surface and prove no probe trace already exists."""

    for tool_name in _required_validation_tools(cases):
        await _ensure_tool(client, tool_name, 30.0)
    langfuse_tool = await _load_langfuse_tool(client, 30.0)
    await _verify_langfuse_posture(client, langfuse_tool, 30.0)
    probe_name = _expected_trace_name(new_run_id(), tenant_id)
    if await _trace_snapshot(
        client,
        langfuse_tool,
        30.0,
        expected_name=probe_name,
    ):
        raise RuntimeError("trace_probe_collision")
    return langfuse_tool


async def _run_validation_case(
    case: ValidationCase,
    *,
    client: Any,
    langfuse_tool: str,
    tenant_id: str,
    defaults: dict[str, int | bool],
    args: argparse.Namespace,
    expected_authority: Any,
) -> CaseResult:
    """Renew the per-mode authority and run one case on its execution path."""

    trace_timeout = float(defaults["trace_timeout_seconds"])
    minimum_ttl_seconds = _direct_case_minimum_authority_ttl(
        case_timeout=args.case_timeout, trace_timeout=trace_timeout
    )
    if case.mode == "direct":
        from agent_utilities.knowledge_graph.core.session import use_session
        from agent_utilities.security.brain_context import use_actor

        validation_session = await _renew_direct_validation_session(
            expected_authority=expected_authority,
            minimum_ttl_seconds=minimum_ttl_seconds,
        )
        with (
            use_actor(validation_session.actor),
            use_session(validation_session),
        ):
            return await _run_direct_case(
                case,
                client=client,
                langfuse_tool=langfuse_tool,
                tenant_id=tenant_id,
                case_timeout=args.case_timeout,
                trace_timeout=trace_timeout,
            )
    await _renew_delegated_validation_session(
        expected_authority=expected_authority,
        minimum_ttl_seconds=minimum_ttl_seconds,
    )
    return await _run_delegated_case(
        case,
        client=client,
        langfuse_tool=langfuse_tool,
        tenant_id=tenant_id,
        max_steps=int(defaults["max_steps"]),
        token_budget=int(defaults["token_budget"]),
        case_timeout=args.case_timeout,
        trace_timeout=trace_timeout,
    )


async def run(args: argparse.Namespace) -> list[CaseResult]:
    defaults, all_cases = load_matrix()
    cases = [case for case in all_cases if args.mode in {"all", case.mode}]

    graph_os_url = _validated_graph_os_url(args)

    from agent_utilities.mcp.client_credentials import child_auth, child_auth_header
    from agent_utilities.mcp.toolset_factory import build_http_toolset

    headers = child_auth_header({})
    identity_session = await _verified_validation_session(
        headers, minimum_ttl_seconds=1
    )
    tenant_id = str(identity_session.tenant)
    expected_authority = identity_session.engine_verified_context()
    toolset = build_http_toolset(
        graph_os_url,
        auth=child_auth({}),
        timeout=args.case_timeout,
        toolset_id="skill-validation",
    )
    results: list[CaseResult] = []
    async with toolset.client as client:
        langfuse_tool = await _prepare_validation_tools(client, cases, tenant_id)
        for case in cases:
            item = await _run_validation_case(
                case,
                client=client,
                langfuse_tool=langfuse_tool,
                tenant_id=tenant_id,
                defaults=defaults,
                args=args,
                expected_authority=expected_authority,
            )
            results.append(item)
            if _SYNC_CALL_POISONED.is_set():
                raise RuntimeError("blocking_sdk_worker_abandoned")
    return results


def _build_argument_parser() -> argparse.ArgumentParser:
    """Declare the full command-line surface of the validation harness."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("direct", "delegated", "all"), default="all")
    parser.add_argument(
        "--graph-os-url",
        default="",
        help="Existing Graph-OS streamable-HTTP URL; defaults to AgentConfig MCP_URL.",
    )
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=120.0,
        help="Per-case wall-clock limit in seconds (1-600).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional Markdown output destination; its location is never recorded.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="Strict signed JSON evidence destination used by --mode all.",
    )
    parser.add_argument("--release-id", default="")
    parser.add_argument("--release-specification-digest", default="")
    parser.add_argument("--promotion-evidence-digest", default="")
    parser.add_argument("--graph-os-digest", default="")
    parser.add_argument("--engine-digest", default="")
    parser.add_argument("--runtime-config-digest", default="")
    parser.add_argument("--runtime-profile-digest", default="")
    parser.add_argument("--model-registry-digest", default="")
    parser.add_argument(
        "--signer-command-ref",
        default=_SIGNER_COMMAND_REFERENCE,
        help="Environment variable containing the external signer JSON argv.",
    )
    parser.add_argument(
        "--verifier-command-ref",
        default=_VERIFIER_COMMAND_REFERENCE,
        help="Environment variable containing the external verifier JSON argv.",
    )
    return parser


def _release_argument_values(args: argparse.Namespace) -> tuple[str, ...]:
    """Return the exact-release argument values in their declared order."""

    return (
        args.release_id,
        args.release_specification_digest,
        args.promotion_evidence_digest,
        args.graph_os_digest,
        args.engine_digest,
        args.runtime_config_digest,
        args.runtime_profile_digest,
        args.model_registry_digest,
    )


def _validate_release_destinations(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Require both publication destinations, colocated and correctly suffixed."""

    if (
        args.report is None
        or args.evidence is None
        or not all(_release_argument_values(args))
    ):
        parser.error(
            "--mode all requires --report, --evidence, --release-id, "
            "--release-specification-digest, --promotion-evidence-digest, "
            "--graph-os-digest, --engine-digest, --runtime-config-digest, "
            "--runtime-profile-digest, and --model-registry-digest"
        )
    if args.report.parent.absolute() != args.evidence.parent.absolute():
        parser.error("--report and --evidence must be published alongside")
    if args.report.suffix.casefold() != ".md" or args.evidence.suffix != ".json":
        parser.error("--report must be Markdown and --evidence must be JSON")


def _validate_release_references(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Require an exact release id, real digests, and signer/verifier refs."""

    if _RELEASE_ID.fullmatch(args.release_id) is None:
        parser.error("--release-id is invalid")
    for option, value in (
        ("--release-specification-digest", args.release_specification_digest),
        ("--promotion-evidence-digest", args.promotion_evidence_digest),
        ("--graph-os-digest", args.graph_os_digest),
        ("--engine-digest", args.engine_digest),
        ("--runtime-config-digest", args.runtime_config_digest),
        ("--runtime-profile-digest", args.runtime_profile_digest),
        ("--model-registry-digest", args.model_registry_digest),
    ):
        if _DIGEST.fullmatch(value) is None:
            parser.error(f"{option} must be a non-sentinel sha256 digest")
    for option, value in (
        ("--signer-command-ref", args.signer_command_ref),
        ("--verifier-command-ref", args.verifier_command_ref),
    ):
        if _COMMAND_REFERENCE.fullmatch(value) is None:
            parser.error(f"{option} must be an environment reference")


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    if not 1.0 <= args.case_timeout <= 600.0:
        parser.error("--case-timeout must be between 1 and 600 seconds")
    if args.mode == "all":
        _validate_release_destinations(parser, args)
        _validate_release_references(parser, args)
    elif args.evidence is not None or any(_release_argument_values(args)):
        parser.error("exact release evidence is emitted only by --mode all")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    static_errors = validate_static_suite()
    if static_errors:
        print(f"Static skill validation failed with {len(static_errors)} issue(s).")
        return 2
    try:
        results = asyncio.run(run(args))
        _validate_result_set(results, mode=args.mode)
        generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        report = render_report(results, generated_at=generated_at)
        if args.mode == "all":
            unsigned = build_evidence(
                results,
                generated_at=generated_at,
                release_id=args.release_id,
                release_specification_digest=args.release_specification_digest,
                promotion_evidence_digest=args.promotion_evidence_digest,
                graph_os_digest=args.graph_os_digest,
                engine_digest=args.engine_digest,
                runtime_config_digest=args.runtime_config_digest,
                runtime_profile_digest=args.runtime_profile_digest,
                model_registry_digest=args.model_registry_digest,
            )
            evidence = sign_and_verify_evidence(
                unsigned,
                signer_reference=args.signer_command_ref,
                verifier_reference=args.verifier_command_ref,
            )
            publish_report(args.evidence, render_evidence(evidence))
            publish_report(args.report, report)
        elif args.report is not None:
            publish_report(args.report, report)
        else:
            print(report)
    except Exception as exc:  # noqa: BLE001 - never print environment-bearing messages
        print(f"Runtime skill validation failed ({type(exc).__name__}).")
        return 2
    passed = sum(result.passed for result in results)
    print(f"Runtime skill validation: {passed}/{len(results)} cases passed.")
    return 0 if passed == len(results) else 1
