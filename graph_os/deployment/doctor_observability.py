"""Observability and Langfuse readiness checks for the deployment doctor."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from .doctor_support import _result


def _require_observability_imports() -> None:
    """Fail closed when the observability stack is not importable at all.

    Kept as an explicit up-front gate because the pre-split check imported every
    dependency before deciding anything: a broken install must still report
    ``error``, never a ``skip``/``fail`` derived from half a stack.
    """
    from agent_utilities.core.transport_security import (  # noqa: F401
        resolve_configured_tls_profile,
    )
    from agent_utilities.observability.custom_observability import (  # noqa: F401
        _same_origin,
    )
    from agent_utilities.observability.langfuse_trust import (  # noqa: F401
        resolve_langfuse_credentials,
        resolve_langfuse_host,
    )
    from agent_utilities.security.cli_secrets import (  # noqa: F401
        resolve_runtime_secret_reference,
    )


def _otel_endpoint(cfg: Any, langfuse_pair: bool) -> tuple[str, bool]:
    """``(endpoint, derived_from_langfuse)`` for the OTLP exporter."""
    from agent_utilities.observability.langfuse_trust import resolve_langfuse_host

    endpoint = str(cfg.otel_exporter_otlp_endpoint or "").strip()
    endpoint_derived = not endpoint and langfuse_pair
    if endpoint_derived:
        endpoint = f"{resolve_langfuse_host('').rstrip('/')}/api/public/otel"
    return endpoint, endpoint_derived


def _otel_transport_posture(cfg: Any) -> SimpleNamespace:
    """Which OTLP endpoint applies and which authentication tier backs it."""
    from agent_utilities.observability.custom_observability import _same_origin
    from agent_utilities.observability.langfuse_trust import resolve_langfuse_host

    langfuse_pair = bool(cfg.langfuse_public_key_ref and cfg.langfuse_secret_key_ref)
    endpoint, endpoint_derived = _otel_endpoint(cfg, langfuse_pair)
    langfuse_auth = bool(
        langfuse_pair and endpoint and _same_origin(endpoint, resolve_langfuse_host(""))
    )
    header_auth = bool(cfg.otel_exporter_otlp_headers_ref)
    key_auth = bool(
        cfg.otel_exporter_otlp_public_key_ref and cfg.otel_exporter_otlp_secret_key_ref
    )
    return SimpleNamespace(
        endpoint=endpoint,
        endpoint_derived=endpoint_derived,
        langfuse_auth=langfuse_auth,
        header_auth=header_auth,
        key_auth=key_auth,
        auth_ready=header_auth or key_auth or langfuse_auth,
    )


def _otel_tls_profile_configured(cfg: Any, langfuse_auth: bool) -> bool:
    """Whether an OTEL TLS profile is configured, directly or via Langfuse."""
    return bool(
        cfg.otel_tls_profile
        or cfg.otel_tls_profile_ref
        or (
            langfuse_auth and (cfg.langfuse_tls_profile or cfg.langfuse_tls_profile_ref)
        )
    )


def _otel_data(cfg: Any, posture: SimpleNamespace, metrics: Any) -> dict[str, Any]:
    """The redacted, metadata-only observability readiness data."""
    return {
        "enabled": bool(cfg.enable_otel),
        "endpoint_configured": bool(posture.endpoint),
        "endpoint_derived_from_langfuse": posture.endpoint_derived,
        "auth_reference_configured": posture.auth_ready,
        "tls_profile_configured": _otel_tls_profile_configured(
            cfg, posture.langfuse_auth
        ),
        "metrics_enabled": bool(metrics),
        "metadata_only": True,
        "redacted": True,
    }


def _otel_prove_credentials(cfg: Any, posture: SimpleNamespace) -> None:
    """Resolve the reference tier actually in use; raises when it is unavailable."""
    from agent_utilities.observability.langfuse_trust import (
        resolve_langfuse_credentials,
    )
    from agent_utilities.security.cli_secrets import resolve_runtime_secret_reference

    if posture.header_auth:
        resolve_runtime_secret_reference(cfg.otel_exporter_otlp_headers_ref)
    elif posture.key_auth:
        resolve_runtime_secret_reference(cfg.otel_exporter_otlp_public_key_ref)
        resolve_runtime_secret_reference(cfg.otel_exporter_otlp_secret_key_ref)
    else:
        resolve_langfuse_credentials(agent_config=cfg)


def _otel_tls_verify_enabled(cfg: Any, langfuse_auth: bool) -> bool:
    """Resolve the OTEL TLS profile (falling back to Langfuse's) and report verify."""
    from agent_utilities.core.transport_security import resolve_configured_tls_profile

    profile_name = cfg.otel_tls_profile
    profile_ref = cfg.otel_tls_profile_ref
    if langfuse_auth and not (profile_name or profile_ref):
        profile_name = cfg.langfuse_tls_profile
        profile_ref = cfg.langfuse_tls_profile_ref
    trust = resolve_configured_tls_profile(
        "OTEL",
        profile_name=profile_name,
        profile_ref=profile_ref,
        config=cfg,
    )
    return trust.verify_enabled


def _check_observability() -> dict[str, Any]:
    from agent_utilities.core.config import AgentConfig, setting
    from agent_utilities.core.profile_guard import is_production_profile

    try:
        _require_observability_imports()
        cfg = AgentConfig()
        production = is_production_profile()
        metrics = setting("GATEWAY_METRICS", False, cast=bool)
        posture = _otel_transport_posture(cfg)
        data = _otel_data(cfg, posture, metrics)
        if not cfg.enable_otel and not production:
            return _result(
                "observability",
                "skip",
                "metadata-only OTLP export is disabled",
                data=data,
            )
        if not posture.endpoint or not posture.auth_ready:
            return _result(
                "observability",
                "fail" if cfg.enable_otel else "warn",
                "OTLP endpoint or authentication references are incomplete",
                remediation=(
                    "Configure OTLP reference-based authentication, or configure the "
                    "Langfuse reference pair for automatic same-origin OTLP wiring."
                ),
                skill="service-observability-provisioner",
                data=data,
            )
        _otel_prove_credentials(cfg, posture)
        data["tls_valid"] = _otel_tls_verify_enabled(cfg, posture.langfuse_auth)
        if production and not metrics:
            return _result(
                "observability",
                "warn",
                "metadata-only OTLP tracing is ready; gateway metrics are disabled",
                remediation="Enable GATEWAY_METRICS for the production profile.",
                skill="service-observability-provisioner",
                data=data,
            )
        return _result(
            "observability",
            "ok",
            "metadata-only OTLP authentication and TLS are ready",
            data=data,
        )
    except Exception as exc:  # noqa: BLE001 - doctor remains privacy-safe
        return _result(
            "observability",
            "error",
            f"OTLP readiness check failed ({type(exc).__name__})",
            data={"redacted": True, "metadata_only": True},
        )


def _run_async_doctor_probe(factory: Callable[[], Any]) -> Any:
    """Run an async probe from either a CLI or an already-running MCP loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    # ``graph_configure(action=system_doctor)`` is itself async. Keep the sync
    # doctor API stable while giving the child transport its own event loop.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="doctor-probe") as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


def _langfuse_rows(payload: Any) -> list[dict[str, Any]]:
    """Extract only bounded trace rows from a successful public API response."""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        rows = payload.get("traces")
    if not isinstance(rows, list):
        return []
    return [row for row in rows[:100] if isinstance(row, dict)]


def _child_call_failed(result: Any) -> bool:
    """A mounted-child tool result reporting an error, under either spelling."""
    return bool(getattr(result, "isError", False)) or bool(
        getattr(result, "is_error", False)
    )


def _langfuse_child_runtime(mux: Any) -> Any:
    """The mounted langfuse child, or ``None`` unless exactly one tool matched."""
    matches = [
        prefixed
        for prefixed, (server, original) in mux.tool_to_server.items()
        if server == "langfuse-mcp" and original == "langfuse_observability"
    ]
    if len(matches) != 1:
        return None
    return mux.children.get("langfuse-mcp")


async def _langfuse_posture_metadata_only(runtime: Any) -> bool:
    """The mounted child must report the metadata-only, no-content posture."""
    from graph_os.fleet.multiplexer import _child_result_payload

    posture_result = await runtime.call_tool(
        "langfuse_observability",
        {"action": "runtime_posture"},
    )
    if _child_call_failed(posture_result):
        return False
    return _child_result_payload(posture_result) == {
        "content_capture_enabled": False,
        "metadata_only": True,
    }


async def _langfuse_trace_read_bounded(runtime: Any) -> bool:
    """Execute the read through the mounted child itself.

    Direct API reachability cannot prove that the child received the same host,
    credential, and TLS contract. The response stays bounded and transient; no
    returned row enters doctor output.
    """
    from graph_os.fleet.multiplexer import _child_result_payload

    trace_result = await runtime.call_tool(
        "langfuse_observability",
        {
            "action": "trace_list",
            "page": 1,
            "limit": 1,
            "fields": "core",
        },
    )
    if _child_call_failed(trace_result):
        return False
    trace_payload = _child_result_payload(trace_result)
    rows = trace_payload.get("data") if isinstance(trace_payload, dict) else None
    return isinstance(rows, list) and len(rows) <= 1


def _probe_langfuse_mcp_visibility(cfg: Any) -> bool:
    """Prove the mounted child can execute the current privacy-safe contract."""
    from pathlib import Path

    from agent_utilities.observability.langfuse_trust import (
        native_langfuse_mcp_config,
    )

    from graph_os.fleet.multiplexer import (
        MCPMultiplexer,
        attest_runtime_child_config,
    )

    child = native_langfuse_mcp_config(agent_config=cfg)
    if child is None:
        return False
    # Bound the diagnostic child startup independently of its operational
    # profile. The cached catalog avoids reading or persisting any local path.
    child = dict(child)
    child["timeout"] = min(float(child.get("timeout", 60.0)), 30.0)
    child = attest_runtime_child_config(child)

    async def probe() -> bool:
        mux = MCPMultiplexer(Path())
        mux._catalog = {"langfuse-mcp": child}
        try:
            await mux.mount_child("langfuse-mcp")
            runtime = _langfuse_child_runtime(mux)
            if runtime is None:
                return False
            if not await _langfuse_posture_metadata_only(runtime):
                return False
            return await _langfuse_trace_read_bounded(runtime)
        finally:
            await mux.aclose()

    return bool(_run_async_doctor_probe(probe))


def _langfuse_api_handshake(cfg: Any) -> tuple[Any, tuple[str, str], str]:
    """``(api, credentials, error_code)``; ``error_code`` is "" only on a proven read.

    The two failure codes stay distinct -- an unreachable API is
    ``api_handshake_failed`` and a reachable API answering with a non-mapping is
    ``api_response_invalid`` -- so the reported code does not depend on which
    fault the caller happens to observe first.
    """
    from agent_utilities.observability.langfuse_trust import (
        resolve_langfuse_credentials,
        resolve_langfuse_requests_transport,
    )
    from langfuse_agent.api_client import LangfuseApi

    try:
        public_key, secret_key = resolve_langfuse_credentials(agent_config=cfg)
        transport_kwargs = resolve_langfuse_requests_transport(agent_config=cfg)
        api = LangfuseApi(
            public_key=public_key,
            secret_key=secret_key,
            host=cfg.langfuse_host,
            timeout=10.0,
            transport_kwargs=transport_kwargs,
        )
        handshake = api.trace_list(page=1, limit=1, fields="core")
    except Exception:  # noqa: BLE001 - expose only a stable diagnostic code
        return None, ("", ""), "api_handshake_failed"
    if not isinstance(handshake, dict):
        return None, ("", ""), "api_response_invalid"
    return api, (public_key, secret_key), ""


def _langfuse_expected_trace_name(source_run_id: str) -> str:
    """The tenant-qualified opaque trace name the exporter will persist."""
    from agent_utilities.usage.privacy import normalize_run_id

    try:
        from agent_utilities.security.brain_context import current_actor

        tenant_id = current_actor().tenant_id
    except Exception:  # noqa: BLE001 - empty tenant namespace is opaque too
        tenant_id = ""
    return f"graph_run:{normalize_run_id(source_run_id, tenant_id=tenant_id)}"


def _langfuse_await_trace(api: Any, expected_name: str, started_at: str) -> bool:
    """Poll for the exported trace by name; ``False`` if it never lands."""
    import time

    for _ in range(10):
        traces = api.trace_list(
            page=1,
            limit=10,
            name=expected_name,
            from_timestamp=started_at,
            order_by="timestamp.desc",
            fields="core,basic",
        )
        if any(row.get("name") == expected_name for row in _langfuse_rows(traces)):
            return True
        time.sleep(1.0)
    return False


def _probe_langfuse_trace_round_trip(
    cfg: Any, api: Any, credentials: tuple[str, str]
) -> tuple[bool, str]:
    """``(round_trip_ok, error_code)``; ``error_code`` is "" only on success.

    The source token is random and never leaves this function. The exporter
    turns it into a tenant-qualified opaque identifier before persistence;
    input and caller metadata are intentionally empty.
    """
    import uuid
    from datetime import UTC, datetime

    from agent_utilities.observability.langfuse_exporter import LangfuseExporter

    public_key, secret_key = credentials
    source_run_id = uuid.uuid4().hex
    expected_name = _langfuse_expected_trace_name(source_run_id)
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    exporter = LangfuseExporter(
        public_key=public_key,
        secret_key=secret_key,
        host=cfg.langfuse_host,
    )
    emitted = exporter.export_graph_run(
        run_id=source_run_id,
        query="",
        status="success",
        metadata={},
    )
    exporter.flush()
    if not emitted:
        return False, "trace_export_failed"
    if _langfuse_await_trace(api, expected_name, started_at):
        return True, ""
    return False, "trace_round_trip_failed"


def _probe_langfuse_live(cfg: Any) -> dict[str, Any]:
    """Prove API, optional MCP, and optional metadata-only trace round trip."""
    result: dict[str, Any] = {
        "live_probed": True,
        "api_reachable": False,
        "mcp_visible": None,
        "trace_round_trip": None,
        "redacted": True,
    }
    api, credentials, handshake_error = _langfuse_api_handshake(cfg)
    if handshake_error:
        result["error_code"] = handshake_error
        return result
    result["api_reachable"] = True

    if cfg.langfuse_mcp_enabled:
        try:
            result["mcp_visible"] = _probe_langfuse_mcp_visibility(cfg)
        except Exception:  # noqa: BLE001 - child details may contain local material
            result["mcp_visible"] = False
        if not result["mcp_visible"]:
            result["error_code"] = "mcp_visibility_failed"
            return result

    if not cfg.trace_export_enabled:
        return result

    try:
        round_trip, trace_error = _probe_langfuse_trace_round_trip(
            cfg, api, credentials
        )
    except Exception:  # noqa: BLE001 - never expose response, endpoint, or identity
        round_trip, trace_error = False, "trace_round_trip_failed"
    result["trace_round_trip"] = round_trip
    if trace_error:
        result["error_code"] = trace_error
    return result


_LANGFUSE_CREDENTIAL_REMEDIATION = (
    "Verify both secret references resolve to real runtime key material; "
    "redaction masks and unresolved templates are rejected locally."
)


def _langfuse_inputs(cfg: Any) -> SimpleNamespace:
    """Which Langfuse credential inputs and integrations are configured.

    A strict secret reference is preferred, but the direct
    ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` pair -- the names
    ``langfuse_agent.auth`` reads for the standalone agent/MCP server -- is
    accepted too; see ``langfuse_credentials_configured()``.
    """
    from agent_utilities.core.config import setting
    from agent_utilities.observability.langfuse_trust import (
        langfuse_provider_contract_ready,
    )

    return SimpleNamespace(
        public_input=bool(cfg.langfuse_public_key_ref)
        or bool(setting("LANGFUSE_PUBLIC_KEY", "")),
        secret_input=bool(cfg.langfuse_secret_key_ref)
        or bool(setting("LANGFUSE_SECRET_KEY", "")),
        enabled=bool(
            cfg.langfuse_mcp_enabled
            or cfg.kg_failure_evolution
            or cfg.trace_export_enabled
            or cfg.langfuse_kg_auto_ingest
        ),
        executable_ready=langfuse_provider_contract_ready(),
    )


def _langfuse_data(cfg: Any, inputs: SimpleNamespace) -> dict[str, Any]:
    """The redacted Langfuse readiness data, before any gate or live probe."""
    return {
        "enabled": inputs.enabled,
        "credential_pair_configured": inputs.public_input and inputs.secret_input,
        "credential_refs_configured": bool(
            cfg.langfuse_public_key_ref and cfg.langfuse_secret_key_ref
        ),
        "credential_material_ready": False,
        "tls_profile_configured": bool(
            cfg.langfuse_tls_profile or cfg.langfuse_tls_profile_ref
        ),
        "persistence_enabled": bool(cfg.langfuse_kg_auto_ingest),
        "persistence_key_ref_configured": bool(cfg.langfuse_persistence_hmac_key_ref),
        "persistence_key_ready": False,
        "mcp_launcher_available": inputs.executable_ready,
        "mcp_launcher_required": bool(cfg.langfuse_mcp_enabled),
        "live_probed": False,
        "redacted": True,
    }


def _langfuse_configuration_gate(
    cfg: Any, inputs: SimpleNamespace, data: dict[str, Any]
) -> dict[str, Any] | None:
    """Skip an unconfigured integration; fail a half-configured credential pair."""
    from agent_utilities.observability.langfuse_trust import (
        langfuse_credentials_configured,
    )

    if not inputs.enabled and not inputs.public_input and not inputs.secret_input:
        return _result(
            "langfuse",
            "skip",
            "Langfuse integration is not configured",
            data=data,
        )
    if (
        inputs.public_input != inputs.secret_input
        or not langfuse_credentials_configured(agent_config=cfg)
    ):
        return _result(
            "langfuse",
            "fail",
            "Langfuse credential configuration is incomplete",
            remediation=(
                "Configure LANGFUSE_PUBLIC_KEY_REF and LANGFUSE_SECRET_KEY_REF "
                "(resolved only at the runtime boundary), or the direct "
                "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY pair used by "
                "langfuse-agent."
            ),
            data=data,
        )
    return None


def _langfuse_credential_gate(cfg: Any, data: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the credential material; anything unresolvable is a fail, never ok."""
    from agent_utilities.observability.langfuse_trust import (
        LangfuseTrustError,
        resolve_langfuse_credentials,
    )

    try:
        resolve_langfuse_credentials(agent_config=cfg)
    except LangfuseTrustError as exc:
        data["error_code"] = exc.reason
    except Exception:  # noqa: BLE001 - never expose provider details
        data["error_code"] = "langfuse_credentials_invalid"
    else:
        data["credential_material_ready"] = True
        return None
    return _result(
        "langfuse",
        "fail",
        "Langfuse credential material is unavailable or invalid",
        remediation=_LANGFUSE_CREDENTIAL_REMEDIATION,
        data=data,
    )


def _langfuse_persistence_gate(cfg: Any, data: dict[str, Any]) -> dict[str, Any] | None:
    """Graph persistence needs its own identity key, and that key must resolve."""
    from agent_utilities.observability.langfuse_trust import (
        resolve_langfuse_persistence_hmac_key,
    )

    if cfg.langfuse_kg_auto_ingest and not cfg.langfuse_persistence_hmac_key_ref:
        return _result(
            "langfuse",
            "fail",
            "Langfuse graph persistence requires a dedicated identity key",
            remediation=(
                "Configure LANGFUSE_PERSISTENCE_HMAC_KEY_REF; the project API "
                "secret is never reused for identity derivation."
            ),
            data=data,
        )
    if not cfg.langfuse_persistence_hmac_key_ref:
        return None
    try:
        resolve_langfuse_persistence_hmac_key(agent_config=cfg)
    except Exception:  # noqa: BLE001 - keep secret-provider details private
        return _result(
            "langfuse",
            "fail",
            "Langfuse persistence identity key is unavailable",
            remediation=(
                "Verify LANGFUSE_PERSISTENCE_HMAC_KEY_REF resolves to at "
                "least 32 bytes at the runtime boundary."
            ),
            data=data,
        )
    data["persistence_key_ready"] = True
    return None


def _langfuse_trust_gate(cfg: Any, data: dict[str, Any]) -> dict[str, Any] | None:
    """TLS verification can never be disabled for the Langfuse transport."""
    from agent_utilities.observability.langfuse_trust import configure_langfuse_trust

    trust = configure_langfuse_trust(agent_config=cfg)
    data["tls_valid"] = trust.valid
    data["custom_trust_configured"] = trust.configured
    if trust.valid:
        return None
    return _result(
        "langfuse",
        "fail",
        f"Langfuse TLS configuration is invalid ({trust.reason or 'invalid'})",
        remediation=(
            "Configure a valid LANGFUSE_TLS_PROFILE_REF or runtime trust "
            "environment; TLS verification cannot be disabled."
        ),
        data=data,
    )


def _langfuse_live_result(
    cfg: Any,
    data: dict[str, Any],
    *,
    probe_live: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prove every enabled live path; an unproven path is a fail, never ok."""
    data.update((probe_live or _probe_langfuse_live)(cfg))
    live_ok = bool(data.get("api_reachable"))
    if cfg.langfuse_mcp_enabled:
        live_ok = live_ok and data.get("mcp_visible") is True
    if cfg.trace_export_enabled:
        live_ok = live_ok and data.get("trace_round_trip") is True
    if not live_ok:
        return _result(
            "langfuse",
            "fail",
            "Langfuse live proof failed",
            remediation=(
                "Verify the runtime secret references, TLS profile, API reachability, "
                "and the Langfuse MCP child installation; diagnostic output is redacted."
            ),
            data=data,
        )
    return _result(
        "langfuse",
        "ok",
        "Langfuse API and enabled live paths are proven",
        data=data,
    )


def _langfuse_ready_result(
    cfg: Any,
    inputs: SimpleNamespace,
    data: dict[str, Any],
    *,
    live: bool,
    probe_live: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The verdict once every static gate has passed."""
    if cfg.langfuse_mcp_enabled and not inputs.executable_ready:
        data["error_code"] = "langfuse_mcp_provider_contract_unavailable"
        return _result(
            "langfuse",
            "fail",
            "Langfuse MCP is enabled but its current child contract is unavailable",
            remediation=(
                "Install the current agent-utilities[serving] artifact in the "
                "GraphOS runtime environment."
            ),
            data=data,
        )
    if not inputs.enabled:
        return _result(
            "langfuse",
            "warn",
            "Langfuse credentials are ready but all integrations are disabled",
            remediation=(
                "Enable metadata-only trace export, MCP access, or governed "
                "failure evolution as required."
            ),
            data=data,
        )
    if not live:
        return _result(
            "langfuse",
            "ok",
            "Langfuse credentials and TLS are statically valid; live proof was not requested",
            data=data,
        )
    return _langfuse_live_result(cfg, data, probe_live=probe_live)


def _check_langfuse(
    live: bool = False,
    *,
    probe_live: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate Langfuse statically, or prove its live privacy-safe paths."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        inputs = _langfuse_inputs(cfg)
        data = _langfuse_data(cfg, inputs)
        # Gates run in this exact order; each one that cannot complete returns a
        # fail rather than letting a later gate report ok on its behalf.
        configuration = _langfuse_configuration_gate(cfg, inputs, data)
        if configuration is not None:
            return configuration
        for gate in (
            _langfuse_credential_gate,
            _langfuse_persistence_gate,
            _langfuse_trust_gate,
        ):
            failure = gate(cfg, data)
            if failure is not None:
                return failure
        return _langfuse_ready_result(
            cfg, inputs, data, live=live, probe_live=probe_live
        )
    except Exception as exc:  # noqa: BLE001 - doctor output remains redacted
        return _result(
            "langfuse",
            "error",
            f"Langfuse readiness check failed ({type(exc).__name__})",
            data={"redacted": True},
        )
