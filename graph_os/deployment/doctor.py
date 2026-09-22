#!/usr/bin/python
"""``agent-utilities doctor`` — one holistic health sweep of a deployment.

Like ``brew doctor`` / ``flutter doctor``: runs a battery of independent checks
across every subsystem, each reporting ok / warn / fail / skip with a concrete
**remediation** — and, where one exists, the **skill or command that fixes it** so
the operator (or Claude) can act or auto-fix. The doctor is a thin *aggregator*: it
composes the diagnostics that already exist (config_doctor, shard topology probe,
backend health_check, the hook doctor, the MCP-config validator, secrets resolution)
rather than re-implementing them.

Each check is defensive — a missing optional dependency or an unreachable service
yields ``skip``/``warn``/``fail`` with guidance, never a crash. ``run_doctor`` returns
a structured report; ``fix=True`` runs the conservative, idempotent auto-remediations
(only checks marked ``auto_fixable``).
"""

from __future__ import annotations

import importlib
import ipaddress
import logging
import stat
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from . import doctor_certification as _doctor_certification
from . import doctor_coverage as _doctor_coverage
from . import doctor_lakehouse as _doctor_lakehouse
from . import doctor_observability as _doctor_observability
from . import doctor_support as _doctor_support

_RANK = _doctor_support._RANK
_prescription = _doctor_support._prescription
_result = _doctor_support._result

logger = logging.getLogger(__name__)


def _validate_skill_certification_regular_inputs(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._validate_skill_certification_regular_inputs(
        *args, **kwargs
    )


def _validate_skill_certification_profile(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._validate_skill_certification_profile(*args, **kwargs)


def _validate_skill_certification_commands(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._validate_skill_certification_commands(*args, **kwargs)


def _validate_skill_certification_material(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._validate_skill_certification_material(*args, **kwargs)


def _check_skill_certification(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._check_skill_certification(*args, **kwargs)


def _cert_required_values(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_required_values(*args, **kwargs)


def _cert_configuration_gate(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_configuration_gate(*args, **kwargs)


def _cert_validated_commands(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_validated_commands(*args, **kwargs)


def _cert_verify_release_manifest(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_verify_release_manifest(*args, **kwargs)


def _cert_verify_artifacts_dir(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_verify_artifacts_dir(*args, **kwargs)


def _cert_verify_bearer_token(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_verify_bearer_token(*args, **kwargs)


def _cert_resolve_prometheus_trust(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._cert_resolve_prometheus_trust(*args, **kwargs)


def _check_production_certification(*args: Any, **kwargs: Any) -> Any:
    return _doctor_certification._check_production_certification(*args, **kwargs)


def _require_observability_imports(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._require_observability_imports(*args, **kwargs)


def _otel_endpoint(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._otel_endpoint(*args, **kwargs)


def _otel_transport_posture(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._otel_transport_posture(*args, **kwargs)


def _otel_tls_profile_configured(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._otel_tls_profile_configured(*args, **kwargs)


def _otel_data(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._otel_data(*args, **kwargs)


def _otel_prove_credentials(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._otel_prove_credentials(*args, **kwargs)


def _otel_tls_verify_enabled(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._otel_tls_verify_enabled(*args, **kwargs)


def _check_observability(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._check_observability(*args, **kwargs)


def _run_async_doctor_probe(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._run_async_doctor_probe(*args, **kwargs)


def _langfuse_rows(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_rows(*args, **kwargs)


def _langfuse_posture_metadata_only(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_posture_metadata_only(*args, **kwargs)


def _langfuse_trace_read_bounded(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_trace_read_bounded(*args, **kwargs)


async def _call_langfuse_child(*args: Any, **kwargs: Any) -> Any:
    return await _doctor_observability._call_langfuse_child(*args, **kwargs)


def _probe_langfuse_mcp_visibility(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._probe_langfuse_mcp_visibility(*args, **kwargs)


def _langfuse_expected_trace_name(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_expected_trace_name(*args, **kwargs)


def _langfuse_await_trace(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_await_trace(*args, **kwargs)


def _probe_langfuse_trace_round_trip(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._probe_langfuse_trace_round_trip(*args, **kwargs)


def _probe_langfuse_live(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._probe_langfuse_live(*args, **kwargs)


def _langfuse_inputs(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_inputs(*args, **kwargs)


def _langfuse_data(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_data(*args, **kwargs)


def _langfuse_configuration_gate(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_configuration_gate(*args, **kwargs)


def _langfuse_credential_gate(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_credential_gate(*args, **kwargs)


def _langfuse_persistence_gate(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_persistence_gate(*args, **kwargs)


def _langfuse_trust_gate(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_trust_gate(*args, **kwargs)


def _langfuse_live_result(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_live_result(*args, **kwargs)


def _langfuse_ready_result(*args: Any, **kwargs: Any) -> Any:
    return _doctor_observability._langfuse_ready_result(*args, **kwargs)


def _check_langfuse(live: bool = False) -> Any:
    return _doctor_observability._check_langfuse(
        live=live, probe_live=_probe_langfuse_live
    )


def _check_workspace_config(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._check_workspace_config(*args, **kwargs)


def _check_bus(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._check_bus(*args, **kwargs)


def _check_skills(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._check_skills(*args, **kwargs)


def _unified_install_tally(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._unified_install_tally(*args, **kwargs)


def _count_generation(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._count_generation(*args, **kwargs)


def _count_provider_materialization(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._count_provider_materialization(*args, **kwargs)


def _count_own_provider(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._count_own_provider(*args, **kwargs)


def _nested_child_is_plain_dir(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._nested_child_is_plain_dir(*args, **kwargs)


def _classify_managed_marker(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._classify_managed_marker(*args, **kwargs)


def _classify_nested_child(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._classify_nested_child(*args, **kwargs)


def _scan_nested_children(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._scan_nested_children(*args, **kwargs)


def _sweep_install_leg(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._sweep_install_leg(*args, **kwargs)


def _unified_install_result(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._unified_install_result(*args, **kwargs)


def _check_unified_install(*args: Any, **kwargs: Any) -> Any:
    return _doctor_coverage._check_unified_install(*args, **kwargs)


def _endpoint_host_port(*args: Any, **kwargs: Any) -> Any:
    return _doctor_lakehouse._endpoint_host_port(*args, **kwargs)


def _probe_tcp(*args: Any, **kwargs: Any) -> Any:
    return _doctor_lakehouse._probe_tcp(*args, **kwargs)


def _probe_http(*args: Any, **kwargs: Any) -> Any:
    return _doctor_lakehouse._probe_http(*args, **kwargs)


def _check_kafka(live: bool = False) -> Any:
    return _doctor_lakehouse._check_kafka(live=live, probe_tcp=_probe_tcp)


def _check_fuseki(live: bool = False) -> Any:
    return _doctor_lakehouse._check_fuseki(live=live, probe_http=_probe_http)


def _check_seaweedfs_s3(live: bool = False) -> Any:
    return _doctor_lakehouse._check_seaweedfs_s3(live=live, probe_tcp=_probe_tcp)


def _lakekeeper_prescription(*args: Any, **kwargs: Any) -> Any:
    return _doctor_lakehouse._lakekeeper_prescription(*args, **kwargs)


def _lakekeeper_gotcha_findings(*args: Any, **kwargs: Any) -> Any:
    return _doctor_lakehouse._lakekeeper_gotcha_findings(*args, **kwargs)


def _check_lakekeeper(live: bool = False) -> Any:
    return _doctor_lakehouse._check_lakekeeper(live=live, probe_http=_probe_http)


def _check_lakekeeper_db(*args: Any, **kwargs: Any) -> Any:
    return _doctor_lakehouse._check_lakekeeper_db(*args, **kwargs)


def _check_trino(live: bool = False) -> Any:
    return _doctor_lakehouse._check_trino(live=live, probe_http=_probe_http)


def _check_spark_runner(live: bool = False) -> Any:
    return _doctor_lakehouse._check_spark_runner(live=live, probe_tcp=_probe_tcp)


# ── individual checks (each returns one _result; never raises) ──────────────
def _optional_extras_present() -> dict[str, bool]:
    """Which optional runtime extras are importable, keyed by human label."""
    return {
        label: importlib.util.find_spec(mod) is not None
        for mod, label in (
            ("rdflib", "owl/sparql"),
            ("psycopg", "postgres"),
            ("stardog", "stardog"),
        )
    }


def _python_env_detail(ver: str, optional: dict[str, bool]) -> str:
    """Render the python_env detail line from the resolved extras map."""
    import platform

    present = [k for k, v in optional.items() if v]
    missing = [k for k, v in optional.items() if not v]
    detail = (
        f"Python {platform.python_version()}, agent-utilities {ver}; "
        f"optional extras present: {present or 'none'}"
    )
    if missing:
        detail += f"; absent (install if needed): {missing}"
    return detail


def _check_python_env() -> dict[str, Any]:
    import sys

    try:
        import agent_utilities

        ver = getattr(agent_utilities, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        return _result(
            "python_env",
            "fail",
            f"agent_utilities not importable ({type(exc).__name__})",
            remediation="pip install agent-utilities[all]",
        )
    optional = _optional_extras_present()
    py_ok = sys.version_info >= (3, 10)
    return _result(
        "python_env",
        "ok" if py_ok else "warn",
        _python_env_detail(str(ver), optional),
        remediation=None if py_ok else "agent-utilities needs Python 3.10+",
        data=optional,
    )


def _check_mcp_sdk_floor() -> dict[str, Any]:
    """The installed `mcp`/`fastmcp` SDK must satisfy the `[mcp]` extra's declared floor.

    A runtime that resolved an older SDK line (`mcp` v1 while the extra declares
    `fastmcp>=4.0.0b1` / SDK v2) previously only failed as a swallowed `ImportError`
    deep inside `child_resilience.py` at serve time (D-ISR-2). This surfaces that
    mismatch here instead through GraphOS's native protocol compatibility gate.
    """
    try:
        from graph_os.fleet.protocol_compat import check_mcp_sdk_floor
    except ImportError as exc:
        return _result(
            "mcp_sdk_floor",
            "skip",
            f"protocol_compat unavailable ({type(exc).__name__})",
        )
    outcome = check_mcp_sdk_floor()
    if outcome["ok"] is None:
        return _result("mcp_sdk_floor", "skip", outcome["detail"])
    if outcome["ok"]:
        return _result("mcp_sdk_floor", "ok", outcome["detail"])
    return _result(
        "mcp_sdk_floor",
        "fail",
        outcome["detail"],
        remediation=(
            "reinstall/rebuild the runtime image so `mcp`/`fastmcp` resolve the "
            "versions declared by GraphOS in pyproject.toml (re-lock and redeploy)"
        ),
    )


def _check_config() -> dict[str, Any]:
    try:
        from graph_os.deployment.config_generator import config_doctor

        rep = config_doctor()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "config", "error", f"config_doctor failed ({type(exc).__name__})"
        )
    healthy = rep.get("healthy")
    profile = rep.get("profile", "?")
    if healthy:
        return _result(
            "config", "ok", f"config healthy for profile {profile!r}", data=rep
        )
    # Tiny's durability findings are advisory.
    status = "warn" if profile == "tiny" else "fail"
    return _result(
        "config",
        status,
        f"config needs attention (profile {profile!r}) — see checks",
        remediation="`setup-config doctor` for detail; `setup-config generate --profile <p>` to (re)seed",
        skill="agent-utilities-deployment",
        data=rep,
    )


def _check_evolution_staging() -> dict[str, Any]:
    """Validate the evolution artifact boundary without exposing its path."""

    import os
    from pathlib import Path

    try:
        from agent_utilities.core.config import AgentConfig

        configured = AgentConfig().evolution_staging_root
    except Exception as exc:  # noqa: BLE001
        return _result(
            "evolution_staging",
            "error",
            f"evolution staging configuration unavailable ({type(exc).__name__})",
            data={"configured": False, "usable": False},
        )
    if not configured:
        return _result(
            "evolution_staging",
            "skip",
            "reviewable evolution artifact staging is not configured",
            remediation=(
                "Set EVOLUTION_STAGING_ROOT in AgentConfig before enabling artifact "
                "drafting or materialization."
            ),
            data={"configured": False, "usable": False},
        )
    try:
        raw = Path(configured).expanduser()
        if raw.is_symlink():
            raise PermissionError("symbolic-link root")
        root = raw.resolve(strict=True)
        if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError("unusable root")
        if os.name != "nt" and root.stat().st_mode & 0o077:
            return _result(
                "evolution_staging",
                "fail",
                "evolution staging is accessible outside the current account",
                remediation="Restrict the configured staging root to mode 0700.",
                data={"configured": True, "usable": False},
            )
    except Exception as exc:  # noqa: BLE001
        return _result(
            "evolution_staging",
            "fail",
            f"evolution staging is unusable ({type(exc).__name__})",
            remediation=(
                "Create a private non-symlink directory, set mode 0700, and point "
                "EVOLUTION_STAGING_ROOT to it."
            ),
            data={"configured": True, "usable": False},
        )
    return _result(
        "evolution_staging",
        "ok",
        "evolution staging is configured, private, and usable",
        data={"configured": True, "usable": True},
    )


def _is_loopback_listener(listener: str, aliases: set[str]) -> bool:
    """Whether a bind address is loopback, treating ``aliases`` as loopback names."""
    try:
        return ipaddress.ip_address(listener).is_loopback
    except ValueError:
        return listener in aliases


def _execution_security_hazards(cfg: Any) -> list[str]:
    """The enabled host-execution escape hatches, in report order."""
    hazards: list[str] = []
    if cfg.kg_loop_allow_host_validation:
        hazards.append("develop_loop_host_validation")
    if cfg.messaging_alert_intake_allow_remote:
        hazards.append("remote_alert_intake")
    return hazards


def _execution_security_cors(cfg: Any, hazards: list[str]) -> dict[str, Any] | None:
    """Credentialed CORS must have an explicit, wildcard-free origin allowlist."""
    if cfg.cors_allow_credentials and (
        not cfg.allowed_origins
        or "*" in {value.strip() for value in cfg.allowed_origins.split(",")}
    ):
        return _result(
            "execution_security",
            "fail",
            "credentialed CORS does not have an explicit origin allowlist",
            remediation=(
                "Set ALLOWED_ORIGINS to exact trusted origins or disable "
                "CORS_ALLOW_CREDENTIALS."
            ),
            data={"unsafe_execution_hazards": hazards},
        )
    return None


def _execution_security_host_allowlist(
    cfg: Any, hazards: list[str]
) -> dict[str, Any] | None:
    """A configured Host-header allowlist may never contain a wildcard."""
    if cfg.allowed_hosts and "*" in {
        value.strip() for value in cfg.allowed_hosts.split(",")
    }:
        return _result(
            "execution_security",
            "fail",
            "the REST Host-header allowlist contains a blocked wildcard",
            remediation=("Replace ALLOWED_HOSTS=* with exact authority names."),
            data={"unsafe_execution_hazards": hazards},
        )
    return None


def _execution_security_listener(cfg: Any, hazards: list[str]) -> dict[str, Any] | None:
    """A non-loopback REST listener needs authentication AND a Host allowlist."""
    listener = cfg.host.strip().strip("[]").lower()
    if _is_loopback_listener(listener, {"localhost"}):
        return None
    if not bool(cfg.auth_jwt_jwks_uri):
        return _result(
            "execution_security",
            "fail",
            "a non-loopback REST listener has no authentication boundary",
            remediation=("Configure JWT authentication or bind HOST to loopback."),
            data={"unsafe_execution_hazards": hazards},
        )
    if not cfg.allowed_hosts:
        return _result(
            "execution_security",
            "fail",
            "a non-loopback REST listener has no Host-header allowlist",
            remediation="Set ALLOWED_HOSTS to the exact served authority names.",
            data={"unsafe_execution_hazards": hazards},
        )
    return None


def _execution_security_alert_intake(
    cfg: Any, hazards: list[str]
) -> dict[str, Any] | None:
    """An enabled alert intake needs a token ref, and loopback unless approved."""
    if cfg.messaging_alert_intake_port is None:
        return None
    if not cfg.messaging_alert_intake_token_ref:
        return _result(
            "execution_security",
            "fail",
            "messaging alert intake is enabled without a token reference",
            remediation=(
                "Set MESSAGING_ALERT_INTAKE_TOKEN_REF to a runtime secret-provider "
                "reference or disable MESSAGING_ALERT_INTAKE_PORT."
            ),
            data={"unsafe_execution_hazards": hazards},
        )
    alert_listener = cfg.messaging_alert_intake_host.strip().strip("[]").lower()
    alert_loopback = _is_loopback_listener(alert_listener, {"localhost", "localhost."})
    if not alert_loopback and not cfg.messaging_alert_intake_allow_remote:
        return _result(
            "execution_security",
            "fail",
            "messaging alert intake requests a non-loopback bind without approval",
            remediation=(
                "Bind MESSAGING_ALERT_INTAKE_HOST to loopback or explicitly set "
                "MESSAGING_ALERT_INTAKE_ALLOW_REMOTE=true behind a protected ingress."
            ),
            data={"unsafe_execution_hazards": hazards},
        )
    return None


def _check_execution_security() -> dict[str, Any]:
    """Surface dangerous host-execution escape hatches without executing them."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "execution_security",
            "error",
            f"execution security configuration unavailable ({type(exc).__name__})",
        )
    hazards = _execution_security_hazards(cfg)
    # Evaluated in this exact order: a doubly-misconfigured deployment must
    # keep reporting the same first failure it always did.
    for guard in (
        _execution_security_cors,
        _execution_security_host_allowlist,
        _execution_security_listener,
        _execution_security_alert_intake,
    ):
        failure = guard(cfg, hazards)
        if failure is not None:
            return failure
    if hazards:
        return _result(
            "execution_security",
            "warn",
            "dangerous host execution escape hatches are enabled",
            remediation=(
                "Disable unsafe host execution flags and configure governed, "
                "isolated execution backends instead."
            ),
            data={"unsafe_execution_hazards": hazards},
        )
    return _result(
        "execution_security",
        "ok",
        "graph-carried validation and RLM model code cannot use unsafe host fallbacks",
        data={"unsafe_execution_hazards": []},
    )


def _check_permission_governance() -> dict[str, Any]:
    """Validate stable identity authority and policy readiness without disclosure."""

    data = {
        "signing_reference_configured": False,
        "signing_authority_ready": False,
        "custom_policy_configured": False,
        "policy_count": 0,
        "identity_verified": False,
        "redacted": True,
    }
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.core.profile_guard import is_production_profile

        cfg = AgentConfig()
        data["signing_reference_configured"] = bool(cfg.permissions_signing_key_ref)
        data["custom_policy_configured"] = bool(cfg.agent_policies_path)
        if not cfg.permissions_signing_key_ref:
            production = is_production_profile(cfg.app_profile)
            return _result(
                "permission_governance",
                "fail" if production else "warn",
                "stable agent-identity signing authority is not configured",
                remediation=(
                    "Set PERMISSIONS_SIGNING_KEY_REF to an env://, vault://, or "
                    "secret:// runtime reference containing at least 32 bytes."
                ),
                data=data,
            )

        from agent_utilities.security.cli_secrets import (
            resolve_runtime_secret_reference,
        )
        from agent_utilities.security.permissions_kernel import (
            AgentRole,
            PermissionsKernel,
        )

        signing_key = resolve_runtime_secret_reference(cfg.permissions_signing_key_ref)
        kernel = PermissionsKernel(
            signing_key=signing_key,
            policies_path=cfg.agent_policies_path,
        )
        identity = kernel.issue_identity("doctor-probe", role=AgentRole.GUEST)
        data.update(
            signing_authority_ready=True,
            policy_count=len(kernel.get_policies()),
            identity_verified=kernel.verify_identity(identity),
        )
        if not data["identity_verified"]:
            raise RuntimeError("identity verification failed")
    except Exception as exc:  # noqa: BLE001 - details and paths stay redacted
        return _result(
            "permission_governance",
            "fail",
            f"permission governance is not ready ({type(exc).__name__})",
            remediation=(
                "Resolve the signing-key reference and repair or remove the "
                "configured policy document; configured policies fail closed."
            ),
            data=data,
        )
    return _result(
        "permission_governance",
        "ok",
        "stable signing authority, policy set, and identity verification are ready",
        data=data,
    )


def _resolve_tls_profile_data() -> tuple[Any, Any, Any, dict[str, Any]]:
    """Returns (cfg, resolver, secrets_client, tls_data)."""
    from agent_utilities.core.config import AgentConfig
    from agent_utilities.core.transport_security import (
        resolve_tls_profile,
        tls_environment_from_config,
    )

    cfg = AgentConfig()
    tls_refs = (
        cfg.tls_profile_ref,
        cfg.tls_profiles_ref,
        cfg.tls_ca_bundle_ref,
        cfg.tls_client_cert_ref,
        cfg.tls_client_key_ref,
        cfg.tls_client_key_password_ref,
        cfg.tls_proxy_url_ref,
        cfg.engine_tls_profile_ref,
    )
    needs_resolver = any(tls_refs) or bool(cfg.external_graph_connectors)
    resolver = None
    secrets_client = None
    if needs_resolver:
        from agent_utilities.security.cli_secrets import (
            resolve_runtime_secret_reference,
        )

        # Environment-backed profiles are already materialized in this
        # process and must not open (or autostart) the durable secret
        # backend. The canonical resolver initializes engine/Vault storage
        # only when the selected reference scheme actually requires it.
        resolver = resolve_runtime_secret_reference
        secrets_client = SimpleNamespace(resolve_ref=resolver)
    runtime_env = tls_environment_from_config(cfg)
    trust = resolve_tls_profile(
        "GLOBAL",
        environ=runtime_env,
        resolver=resolver,
    )
    tls_data = {
        "configured": trust.configured,
        "ready": True,
        "source": trust.source,
        "verify_enabled": trust.verify_enabled,
        "system_trust": trust.system_trust,
        "custom_ca": bool(trust.ca_bundle_path or trust.ca_directory),
        "mtls": bool(trust.client_cert_path),
        "proxy": bool(trust.proxy_url),
    }
    trust.cleanup()
    return cfg, resolver, secrets_client, tls_data


def _resolve_engine_transport_data(cfg: Any, resolver: Any) -> dict[str, Any]:
    from agent_utilities.core.transport_security import resolve_tls_profile
    from agent_utilities.knowledge_graph.core.engine_transport import (
        EngineTransportError,
        engine_client_transport_kwargs,
    )
    from agent_utilities.knowledge_graph.core.shard_topology import (
        resolve_endpoints,
    )

    engine_endpoints = resolve_endpoints(cfg)
    engine_tls_configured = any(
        endpoint.startswith("tls://") for endpoint in engine_endpoints
    ) or bool(cfg.engine_tls_profile or cfg.engine_tls_profile_ref)
    engine_data: dict[str, Any] = {
        "configured": engine_tls_configured,
        "ready": True,
        "endpoint_count": len(engine_endpoints),
        "verify_enabled": True,
        "custom_ca": False,
        "mtls": False,
    }
    for endpoint in engine_endpoints:
        if not endpoint.startswith("tcp://"):
            continue
        try:
            engine_client_transport_kwargs(endpoint, config=cfg)
        except EngineTransportError:
            engine_data["ready"] = False
            break
    if engine_tls_configured:
        engine_trust = resolve_tls_profile(
            "ENGINE",
            profile_name=cfg.engine_tls_profile,
            profile_ref=cfg.engine_tls_profile_ref,
            resolver=resolver,
        )
        engine_data.update(
            verify_enabled=engine_trust.verify_enabled,
            custom_ca=bool(engine_trust.ca_bundle_path or engine_trust.ca_directory),
            mtls=bool(engine_trust.client_cert_path),
        )
        engine_trust.cleanup()
    return engine_data


def _connector_name_uniqueness(cfg: Any) -> tuple[bool, bool]:
    source_aliases = [
        connector.source_alias for connector in cfg.external_graph_connectors
    ]
    connection_names = [connector.name for connector in cfg.external_graph_connectors]
    source_aliases_unique = (
        bool(all(source_aliases) and len(set(source_aliases)) == len(source_aliases))
        if source_aliases
        else True
    )
    connection_names_unique = (
        bool(
            all(connection_names)
            and len(set(connection_names)) == len(connection_names)
        )
        if connection_names
        else True
    )
    return source_aliases_unique, connection_names_unique


def _check_property_bundle_ready() -> bool:
    try:
        from agent_utilities.knowledge_graph.ontology.connector_manifest_gate import (
            precheck_source,
        )

        bundle = precheck_source("external_graph")
        return bool(bundle.get("checked") and bundle.get("ok"))
    except Exception:
        return False


def _build_connector_sync_policy(connector: Any, property_graph: bool) -> dict | None:
    if not property_graph:
        return None
    return {
        "allow_empty_snapshot": bool(getattr(connector, "allow_empty_snapshot", False)),
        "max_pages": int(getattr(connector, "ingest_max_pages", 100)),
        "max_row_bytes": int(getattr(connector, "ingest_max_row_bytes", 1_048_576)),
        "max_total_bytes": int(
            getattr(connector, "ingest_max_total_bytes", 16_777_216)
        ),
        "max_nesting_depth": int(getattr(connector, "ingest_max_nesting_depth", 16)),
        "max_collection_items": int(
            getattr(connector, "ingest_max_collection_items", 10_000)
        ),
        "page_size": int(getattr(connector, "ingest_page_size", 500)),
        "reconcile_deletions": bool(getattr(connector, "reconcile_deletions", True)),
        "sync_mode": str(getattr(connector, "sync_mode", "auto")),
    }


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constants are not supported")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON keys are not supported")
        value[key] = item
    return value


def _parse_bounded_secret_json(resolved: Any) -> Any:
    import json

    if len(str(resolved).encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("external profile exceeds its bound")
    return json.loads(
        str(resolved),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_json_keys,
    )


def _graphql_connection_ref_ready(parsed: dict[str, Any]) -> bool:
    from agent_utilities.knowledge_graph.ingestion.graphql_connection import (
        GRAPHQL_CONNECTION_PROFILE_FORMAT,
    )

    return parsed.get(
        "profile_format"
    ) == GRAPHQL_CONNECTION_PROFILE_FORMAT and isinstance(parsed.get("endpoint"), str)


def _graphql_mapping_ref_ready(parsed: dict[str, Any]) -> bool:
    from agent_utilities.knowledge_graph.ingestion.graphql_connection import (
        GRAPHQL_MAPPING_POLICY_FORMAT,
    )

    discovery = parsed.get("discovery") or {}
    return (
        parsed.get("profile_format") == GRAPHQL_MAPPING_POLICY_FORMAT
        and isinstance(parsed.get("operations", {}), dict)
        and bool(
            parsed.get("operations")
            or (isinstance(discovery, dict) and discovery.get("enabled") is True)
        )
    )


def _graphql_auth_ref_ready(parsed: dict[str, Any]) -> bool:
    from agent_utilities.knowledge_graph.ingestion.graphql_connection import (
        GRAPHQL_AUTH_PROFILE_FORMAT,
    )

    return parsed.get("profile_format") == GRAPHQL_AUTH_PROFILE_FORMAT and isinstance(
        parsed.get("headers", {}), dict
    )


def _graphql_ref_format_ready(label: str, parsed: dict[str, Any]) -> bool:
    if label == "connection":
        return _graphql_connection_ref_ready(parsed)
    if label == "mapping":
        return _graphql_mapping_ref_ready(parsed)
    if label == "auth":
        return _graphql_auth_ref_ready(parsed)
    # No graphql-specific narrowing for this label (e.g. "variables") --
    # `ready` (already True, the caller's gate) is left unchanged.
    return True


def _resolve_one_connector_ref(
    connector: Any, label: str, ref: Any, resolver: Any
) -> tuple[bool, Any]:
    """Returns (ready, resolved_mapping_policy_if_this_was_the_mapping_ref)."""
    from agent_utilities.core.transport_security import resolve_tls_profile

    resolved_mapping_policy = None
    try:
        if label == "tls":
            connector_trust = resolve_tls_profile(
                "EXTERNAL_GRAPH",
                profile_ref=str(ref),
                resolver=resolver,
            )
            try:
                ready = connector_trust.verify_enabled
            finally:
                connector_trust.cleanup()
            resolved = None
        else:
            resolved = resolver(ref) if resolver is not None else None
            ready = bool(resolved)
        if label in {"auth", "connection", "mapping", "variables"} and ready:
            parsed = _parse_bounded_secret_json(resolved)
            ready = isinstance(parsed, dict)
            if label == "mapping" and ready:
                resolved_mapping_policy = parsed
            if ready and connector.backend == "graphql":
                ready = _graphql_ref_format_ready(label, parsed)
    except Exception:
        ready = False
    return ready, resolved_mapping_policy


def _resolve_connector_refs(
    connector: Any, refs: dict[str, Any], resolver: Any, unresolved: list[int]
) -> tuple[dict[str, bool | None], Any]:
    readiness: dict[str, bool | None] = {}
    resolved_mapping_policy = None
    for label, ref in refs.items():
        if ref is None:
            readiness[label] = None
            continue
        ready, this_mapping_policy = _resolve_one_connector_ref(
            connector, label, ref, resolver
        )
        if this_mapping_policy is not None:
            resolved_mapping_policy = this_mapping_policy
        readiness[label] = ready
        unresolved[0] += int(not ready)
    return readiness, resolved_mapping_policy


def _bump_graphql_generated_mapping(
    connector: Any, readiness: dict[str, Any], unresolved: list[int]
) -> None:
    if connector.backend != "graphql":
        return
    generated_bootstrap = (
        connector.mapping_policy_ref is None and connector.allow_introspection
    )
    readiness["generated_mapping"] = (
        generated_bootstrap if connector.mapping_policy_ref is None else None
    )
    unresolved[0] += int(
        connector.mapping_policy_ref is None and not connector.allow_introspection
    )


def _resolve_graphql_mapping_status(
    connector: Any,
    resolver: Any,
    secrets_client: Any,
    readiness: dict[str, Any],
    unresolved: list[int],
) -> tuple[Any, str]:
    from agent_utilities.knowledge_graph.ingestion.graphql_connection import (
        GraphQLSourceAdapter,
        graphql_mapping_profile_status,
    )

    source = GraphQLSourceAdapter(
        connection=connector.name,
        source_alias=connector.source_alias,
        connection_profile_ref=connector.connection_profile_ref,
        mapping_policy_ref=str(connector.mapping_policy_ref or ""),
        auth_profile_ref=connector.auth_profile_ref,
        tls_profile_ref=connector.tls_profile_ref,
        variables_ref=getattr(connector, "variables_ref", None),
        allow_introspection=connector.allow_introspection,
        allow_empty_snapshot=bool(getattr(connector, "allow_empty_snapshot", False)),
        resolver=resolver,
    )
    try:
        source.validate_runtime_profiles()
    except Exception:
        readiness["runtime_contract"] = False
        unresolved[0] += 1
        raise
    readiness["runtime_contract"] = True
    mapping_status = graphql_mapping_profile_status(
        source,
        connection=connector.name,
        secret_store=secrets_client,
    )
    mapping_policy_drift = str(mapping_status.get("mapping_drift") or "unknown")
    return mapping_status, mapping_policy_drift


def _resolve_property_graph_mapping_status(
    connector: Any,
    secrets_client: Any,
    resolved_mapping_policy: Any,
    sync_policy: dict | None,
) -> tuple[Any, str]:
    from agent_utilities.knowledge_graph.ingestion.external_graph_schema import (
        external_mapping_policy_digest,
        mapping_profile_status,
    )

    if connector.mapping_policy_ref is None:
        current_policy = {}
    elif resolved_mapping_policy is not None:
        current_policy = resolved_mapping_policy
    else:
        current_policy = None
    current_policy_digest = (
        external_mapping_policy_digest({**current_policy, "sync": sync_policy})
        if current_policy is not None and sync_policy is not None
        else None
    )
    mapping_status = mapping_profile_status(
        connector.name,
        secret_store=secrets_client,
        runtime_policy_digest=current_policy_digest,
    )
    mapping_policy_drift = (
        str(mapping_status.get("mapping_drift") or "unknown")
        if current_policy_digest is not None
        else "unknown"
    )
    return mapping_status, mapping_policy_drift


def _resolve_connector_mapping_lifecycle(
    connector: Any,
    resolver: Any,
    secrets_client: Any,
    resolved_mapping_policy: Any,
    sync_policy: dict | None,
    readiness: dict[str, Any],
    unresolved: list[int],
) -> tuple[str, str | None]:
    lifecycle = "not_found"
    mapping_policy_drift: str | None = None
    if secrets_client is None:
        return lifecycle, mapping_policy_drift
    try:
        if connector.backend == "graphql":
            mapping_status, mapping_policy_drift = _resolve_graphql_mapping_status(
                connector, resolver, secrets_client, readiness, unresolved
            )
        else:
            mapping_status, mapping_policy_drift = (
                _resolve_property_graph_mapping_status(
                    connector, secrets_client, resolved_mapping_policy, sync_policy
                )
            )
        lifecycle = str(mapping_status.get("status") or "not_found")
    except Exception:
        lifecycle = "unavailable"
        mapping_policy_drift = "unknown"
    return lifecycle, mapping_policy_drift


def _build_connector_result_dict(
    connector: Any,
    readiness: dict[str, Any],
    lifecycle: str,
    mapping_policy_drift: str | None,
    property_bundle_ready: bool | None,
    property_graph: bool,
    sync_policy: dict | None,
) -> dict[str, Any]:
    return {
        "backend": connector.backend,
        "refs_ready": readiness,
        "mapping_lifecycle": lifecycle,
        "mapping_policy_drift": mapping_policy_drift,
        "capability_bundle_ready": (property_bundle_ready if property_graph else None),
        "sync_policy": sync_policy,
        "semantic_mapping": connector.semantic_mapping,
        "generated_mapping": bool(
            connector.backend == "graphql"
            and connector.mapping_policy_ref is None
            and connector.allow_introspection
        ),
        "authoritative_empty_approval": bool(
            connector.backend == "graphql"
            and getattr(connector, "allow_empty_snapshot", False)
        ),
        "approval_required": connector.require_approval,
        "drift_policy": connector.schema_drift_policy,
    }


def _evaluate_one_connector(
    connector: Any,
    resolver: Any,
    secrets_client: Any,
    property_bundle_ready_holder: list[bool | None],
    unresolved: list[int],
) -> dict[str, Any]:
    property_graph = connector.backend != "graphql"
    if property_graph and property_bundle_ready_holder[0] is None:
        property_bundle_ready_holder[0] = _check_property_bundle_ready()
    sync_policy = _build_connector_sync_policy(connector, property_graph)
    refs = {
        "connection": connector.connection_profile_ref,
        "mapping": connector.mapping_policy_ref,
        "tls": connector.tls_profile_ref,
        "auth": connector.auth_profile_ref,
        "variables": getattr(connector, "variables_ref", None),
    }
    readiness, resolved_mapping_policy = _resolve_connector_refs(
        connector, refs, resolver, unresolved
    )
    _bump_graphql_generated_mapping(connector, readiness, unresolved)
    lifecycle, mapping_policy_drift = _resolve_connector_mapping_lifecycle(
        connector,
        resolver,
        secrets_client,
        resolved_mapping_policy,
        sync_policy,
        readiness,
        unresolved,
    )
    return _build_connector_result_dict(
        connector,
        readiness,
        lifecycle,
        mapping_policy_drift,
        property_bundle_ready_holder[0],
        property_graph,
        sync_policy,
    )


def _transport_security_status(
    unresolved: int,
    bundle_unready: int,
    source_aliases_unique: bool,
    connection_names_unique: bool,
    engine_data: dict[str, Any],
    verification_disabled: bool,
    lifecycle_unready: int,
) -> str:
    if (
        unresolved
        or bundle_unready
        or not source_aliases_unique
        or not connection_names_unique
        or not engine_data["ready"]
    ):
        return "fail"
    if verification_disabled or lifecycle_unready:
        return "warn"
    return "ok"


def _transport_security_detail(
    unresolved: int,
    bundle_unready: int,
    source_aliases_unique: bool,
    connection_names_unique: bool,
    engine_data: dict[str, Any],
    verification_disabled: bool,
    lifecycle_unready: int,
) -> str:
    if not engine_data["ready"]:
        return "native engine transport policy is not ready"
    if unresolved:
        return f"{unresolved} configured external profile reference(s) are unresolved"
    if bundle_unready:
        return f"{bundle_unready} property-graph capability bundle(s) are unready"
    if not source_aliases_unique:
        return "external graph source aliases are not unique"
    if not connection_names_unique:
        return "external graph connection names are not unique"
    if verification_disabled:
        return "one or more runtime transports have TLS verification disabled"
    if lifecycle_unready:
        return f"{lifecycle_unready} external mapping lifecycle(s) require approval"
    return "runtime trust profile ready"


def _finalize_transport_security_result(
    connectors: list[dict[str, Any]],
    unresolved: int,
    source_aliases_unique: bool,
    connection_names_unique: bool,
    engine_data: dict[str, Any],
    tls_data: dict[str, Any],
) -> dict[str, Any]:
    lifecycle_unready = sum(
        1
        for connector in connectors
        if connector.get("mapping_lifecycle") != "approved"
        or connector.get("mapping_policy_drift") == "detected"
    )
    bundle_unready = sum(
        1
        for connector in connectors
        if connector.get("capability_bundle_ready") is False
    )
    verification_disabled = not tls_data["verify_enabled"] or not bool(
        engine_data["verify_enabled"]
    )
    status = _transport_security_status(
        unresolved,
        bundle_unready,
        source_aliases_unique,
        connection_names_unique,
        engine_data,
        verification_disabled,
        lifecycle_unready,
    )
    detail = _transport_security_detail(
        unresolved,
        bundle_unready,
        source_aliases_unique,
        connection_names_unique,
        engine_data,
        verification_disabled,
        lifecycle_unready,
    )
    return _result(
        "transport_security",
        status,
        detail,
        remediation=(
            None
            if status == "ok"
            else (
                "Repair unresolved secret refs or signed capability bundles, enable "
                "verified TLS, or complete the discover/propose/approve lifecycle."
            )
        ),
        data={
            "tls": tls_data,
            "native_engine_tls": engine_data,
            "external_graph_connectors": connectors,
            "external_graph_source_aliases_unique": source_aliases_unique,
            "external_graph_connection_names_unique": connection_names_unique,
        },
    )


def _check_transport_security() -> dict[str, Any]:
    """Validate TLS/auth handoff using only redacted readiness metadata."""
    try:
        cfg, resolver, secrets_client, tls_data = _resolve_tls_profile_data()
        engine_data = _resolve_engine_transport_data(cfg, resolver)
        source_aliases_unique, connection_names_unique = _connector_name_uniqueness(cfg)

        connectors: list[dict[str, Any]] = []
        unresolved = [0]
        property_bundle_ready_holder: list[bool | None] = [None]
        for connector in cfg.external_graph_connectors:
            connectors.append(
                _evaluate_one_connector(
                    connector,
                    resolver,
                    secrets_client,
                    property_bundle_ready_holder,
                    unresolved,
                )
            )
    except Exception as exc:  # noqa: BLE001 - doctor must remain defensive
        return _result(
            "transport_security",
            "fail",
            f"transport profile validation failed ({type(exc).__name__})",
            remediation=(
                "Configure a named TLS profile with secret refs; do not place "
                "endpoints, credentials, certificate material, or local paths in config."
            ),
            data={"ready": False},
        )

    return _finalize_transport_security_result(
        connectors,
        unresolved[0],
        source_aliases_unique,
        connection_names_unique,
        engine_data,
        tls_data,
    )


def _check_google_workspace_oauth() -> dict[str, Any]:
    """Validate optional OAuth bootstrap without disclosing tenant configuration."""

    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "google_workspace_oauth",
            "error",
            f"OAuth configuration unavailable ({type(exc).__name__})",
        )
    client_ready = bool(cfg.google_workspace_oauth_client_id)
    broker_ready = bool(cfg.google_workspace_oauth_broker_url)
    data = {"client_id_configured": client_ready, "broker_configured": broker_ready}
    if not client_ready and not broker_ready:
        return _result(
            "google_workspace_oauth",
            "skip",
            "Google Workspace OAuth is not configured",
            data=data,
        )
    if not (client_ready and broker_ready):
        return _result(
            "google_workspace_oauth",
            "fail",
            "Google Workspace OAuth configuration is incomplete",
            remediation=(
                "Set both GOOGLE_WORKSPACE_OAUTH_CLIENT_ID and the HTTPS-only "
                "GOOGLE_WORKSPACE_OAUTH_BROKER_URL through AgentConfig/XDG runtime config."
            ),
            data=data,
        )
    return _result(
        "google_workspace_oauth",
        "ok",
        "Google Workspace OAuth runtime connection points are configured",
        data=data,
    )


def _egress_tls_profiles(cfg: Any) -> dict[str, Any]:
    """Resolve the model/embedding/OAuth2-token TLS profiles, in that order."""
    from agent_utilities.core.transport_security import (
        resolve_tls_profile,
        tls_environment_from_config,
    )

    tls_environment = tls_environment_from_config(cfg)
    return {
        "model": resolve_tls_profile(
            "model",
            profile_name=cfg.model_tls_profile,
            profile_ref=cfg.model_tls_profile_ref,
            environ=tls_environment,
        ),
        "embedding": resolve_tls_profile(
            "embedding",
            profile_name=cfg.embedding_tls_profile,
            profile_ref=cfg.embedding_tls_profile_ref,
            environ=tls_environment,
        ),
        "oauth2_token": resolve_tls_profile(
            "oauth2-token",
            profile_name=cfg.oauth2_token_tls_profile,
            profile_ref=cfg.oauth2_token_tls_profile_ref,
            environ=tls_environment,
        ),
    }


def _egress_tls_data(cfg: Any) -> tuple[dict[str, Any], bool]:
    """(redacted model-transport data, whether any model proxy is configured)."""
    profiles = _egress_tls_profiles(cfg)
    proxy_configured = bool(
        profiles["model"].proxy_url
        or profiles["embedding"].proxy_url
        or profiles["oauth2_token"].proxy_url
    )
    data: dict[str, Any] = {}
    for label, profile in profiles.items():
        data[f"{label}_verify_enabled"] = profile.verify_enabled
        data[f"{label}_custom_ca"] = bool(
            profile.ca_bundle_path or profile.ca_directory
        )
        data[f"{label}_mtls"] = bool(profile.client_cert_path)
    data["oauth2_model_count"] = sum(
        bool(getattr(model, "oauth2", None))
        for model in (*cfg.chat_models, *cfg.embedding_models)
    )
    data["model_proxy_configured"] = proxy_configured
    for profile in profiles.values():
        profile.cleanup()
    return data, proxy_configured


def _check_source_egress() -> dict[str, Any]:
    """Report the shared SSRF/redirect/body boundary without exposing hosts."""
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.protocols.source_connectors.http_safety import (
            normalize_allowed_hosts,
        )

        cfg = AgentConfig()
        private_hosts = normalize_allowed_hosts(cfg.source_http_allowed_private_hosts)
        redirect_hosts = normalize_allowed_hosts(cfg.source_http_allowed_redirect_hosts)
        model_private_hosts = normalize_allowed_hosts(
            cfg.model_http_allowed_private_hosts
        )
        model_tls_data, model_proxy_configured = _egress_tls_data(cfg)
    except Exception as exc:  # noqa: BLE001 - doctor must remain defensive
        return _result(
            "source_egress",
            "fail",
            f"source egress policy is invalid ({type(exc).__name__})",
            remediation=(
                "Use exact hostnames (no URLs or wildcards), bounded response/redirect "
                "limits, and keep browser fetching disabled unless explicitly required."
            ),
            data={"ready": False},
        )

    browser_enabled = bool(cfg.source_http_allow_browser_fetch)
    if model_proxy_configured:
        return _result(
            "source_egress",
            "fail",
            "model transport profile is incompatible with DNS-pinned egress",
            remediation=(
                "Remove the model/embedder/OAuth2 token proxy and use direct TLS with "
                "a runtime CA or mTLS profile so destination DNS and peer identity "
                "can be pinned."
            ),
            data={"ready": False, **model_tls_data},
        )
    return _result(
        "source_egress",
        "warn" if browser_enabled else "ok",
        (
            "bounded source egress is active; browser-backed fetching is explicitly enabled"
            if browser_enabled
            else "bounded source egress is active; private hosts and cross-host redirects are denied by default"
        ),
        remediation=(
            "Disable SOURCE_HTTP_ALLOW_BROWSER_FETCH when rendered-page acquisition is not required."
            if browser_enabled
            else None
        ),
        data={
            "ready": True,
            "private_host_allowlist_count": len(private_hosts),
            "redirect_host_allowlist_count": len(redirect_hosts),
            "model_private_host_allowlist_count": len(model_private_hosts),
            "max_response_bytes": cfg.source_http_max_response_bytes,
            "max_redirects": cfg.source_http_max_redirects,
            "browser_fetch_enabled": browser_enabled,
            **model_tls_data,
        },
    )


def _eunomia_embedded_result(cfg: Any) -> dict[str, Any]:
    """Verdict for ``EUNOMIA_TYPE=embedded``: the policy file must be readable."""
    from pathlib import Path

    policy = str(cfg.eunomia_policy_file or "mcp_policies.json")
    ready = Path(policy).expanduser().is_file()
    return _result(
        "eunomia",
        "ok" if ready else "fail",
        (
            "embedded native MCP policy is configured"
            if ready
            else "embedded MCP policy file is unavailable"
        ),
        remediation=(
            None
            if ready
            else "Set EUNOMIA_POLICY_FILE to a runtime-mounted policy document."
        ),
        data={"mode": "embedded", "ready": ready},
    )


def _eunomia_require_bounded_endpoint(cfg: Any, private_hosts: Any) -> None:
    """Raise unless the remote PDP endpoint is present, allowlisted, and HTTPS."""
    from urllib.parse import urlsplit

    from agent_utilities.protocols.source_connectors.http_safety import (
        require_safe_source_url,
    )

    endpoint = str(cfg.eunomia_remote_url or "")
    if not endpoint:
        raise ValueError("remote endpoint is missing")
    host = require_safe_source_url(
        endpoint,
        allowed_private_hosts=private_hosts,
        resolve_dns=False,
    )
    parsed = urlsplit(endpoint)
    insecure_transport = parsed.scheme == "http" and host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if insecure_transport:
        raise ValueError("remote endpoint requires HTTPS")


def _eunomia_tls_data(cfg: Any) -> dict[str, Any]:
    """Redacted TLS posture for the remote PDP transport."""
    from agent_utilities.core.transport_security import (
        resolve_tls_profile,
        tls_environment_from_config,
    )

    trust = resolve_tls_profile(
        "eunomia",
        profile_name=cfg.eunomia_tls_profile,
        profile_ref=cfg.eunomia_tls_profile_ref,
        environ=tls_environment_from_config(cfg),
    )
    tls_data = {
        "verify_enabled": trust.verify_enabled,
        "custom_ca": bool(trust.ca_bundle_path or trust.ca_directory),
        "mtls": bool(trust.client_cert_path),
        "proxy_configured": bool(trust.proxy_url),
    }
    trust.cleanup()
    return tls_data


def _eunomia_remote_result(cfg: Any, private_hosts: Any) -> dict[str, Any]:
    """Verdict for ``EUNOMIA_TYPE=remote``; raises when the endpoint is unsound."""
    _eunomia_require_bounded_endpoint(cfg, private_hosts)
    tls_data = _eunomia_tls_data(cfg)
    if tls_data["proxy_configured"]:
        raise ValueError("remote policy proxy is incompatible with DNS pinning")
    return _result(
        "eunomia",
        "ok",
        ("remote native MCP policy authorization is bounded and TLS-verified"),
        remediation=None,
        data={
            "mode": "remote",
            "ready": True,
            "private_host_allowlist_count": len(private_hosts),
            "api_key_ref_configured": bool(cfg.eunomia_api_key_ref),
            "timeout_seconds": cfg.eunomia_timeout_seconds,
            "max_response_bytes": cfg.eunomia_max_response_bytes,
            "bulk_check_max": cfg.eunomia_bulk_check_max,
            **tls_data,
        },
    )


def _check_eunomia() -> dict[str, Any]:
    """Validate the native policy-decision-point configuration without I/O."""
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.protocols.source_connectors.http_safety import (
            normalize_allowed_hosts,
        )

        cfg = AgentConfig()
        mode = cfg.eunomia_type
        private_hosts = normalize_allowed_hosts(cfg.eunomia_allowed_private_hosts)
        if mode == "none":
            return _result(
                "eunomia",
                "ok",
                "native MCP policy authorization is explicitly disabled",
                data={"mode": "none", "ready": True},
            )
        if mode == "embedded":
            return _eunomia_embedded_result(cfg)
        return _eunomia_remote_result(cfg, private_hosts)
    except Exception as exc:  # noqa: BLE001 - doctor is a defensive boundary
        return _result(
            "eunomia",
            "fail",
            f"native MCP policy configuration is invalid ({type(exc).__name__})",
            remediation=(
                "Configure EUNOMIA_TYPE plus either a runtime policy file or a "
                "bounded HTTPS endpoint, exact private-host allowlist, secret ref, "
                "and EUNOMIA TLS profile in AgentConfig/XDG."
            ),
            data={"ready": False, "redacted": True},
        )


def _inventory_format_ready(inventory_path: Any) -> bool:
    """Whether the inventory file parses as a bounded YAML mapping."""
    try:
        import yaml

        with inventory_path.open("rb") as stream:
            raw_inventory = stream.read(8 * 1024 * 1024 + 1)
        if len(raw_inventory) > 8 * 1024 * 1024:
            return False
        return isinstance(yaml.safe_load(raw_inventory.decode("utf-8")), dict)
    except Exception:  # noqa: BLE001 - readiness is redacted
        return False


def _inventory_readiness(raw_path: Any) -> tuple[bool, bool]:
    """``(file_ready, format_ready)`` for the optional infrastructure inventory."""
    from pathlib import Path

    try:
        inventory_path = Path(str(raw_path)).expanduser()
        file_ready = inventory_path.is_file()
    except (OSError, ValueError):
        return False, False
    if not file_ready:
        return False, False
    return True, _inventory_format_ready(inventory_path)


def _media_endpoint_count(cfg: Any) -> int:
    """How many of the nine optional media endpoints are configured."""
    return sum(
        bool(value)
        for value in (
            cfg.comfyui_url,
            cfg.xtts_url,
            cfg.openai_tts_url,
            cfg.whisper_url,
            cfg.faster_whisper_url,
            cfg.flux_url,
            cfg.sd35_url,
            cfg.hunyuan_url,
            cfg.svd_url,
        )
    )


def _check_runtime_integrations() -> dict[str, Any]:
    """Validate optional fleet, inventory, and media configuration offline.

    Endpoint identities and inventory paths are runtime-only material. This
    check reports aggregate configuration readiness and local file availability
    without returning any configured value or making a network request.
    """
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        inventory_configured = bool(cfg.infra_inventory_path)
        inventory_file_ready = False
        inventory_format_ready = False
        if inventory_configured:
            inventory_file_ready, inventory_format_ready = _inventory_readiness(
                cfg.infra_inventory_path
            )
        fleet_template_configured = bool(cfg.fleet_mcp_url_template)
        media_endpoint_count = _media_endpoint_count(cfg)
    except Exception as exc:  # noqa: BLE001 - doctor must remain defensive
        return _result(
            "runtime_integrations",
            "fail",
            f"runtime integration configuration is invalid ({type(exc).__name__})",
            remediation=(
                "Use bounded http(s) base URLs without inline credentials, a fleet "
                "template containing {server}, and a valid runtime inventory path."
            ),
            data={"ready": False, "redacted": True},
        )

    return _runtime_integrations_result(
        inventory_configured=inventory_configured,
        inventory_file_ready=inventory_file_ready,
        inventory_format_ready=inventory_format_ready,
        fleet_template_configured=fleet_template_configured,
        media_endpoint_count=media_endpoint_count,
    )


def _runtime_integrations_result(
    *,
    inventory_configured: bool,
    inventory_file_ready: bool,
    inventory_format_ready: bool,
    fleet_template_configured: bool,
    media_endpoint_count: int,
) -> dict[str, Any]:
    """Turn the resolved runtime-integration readiness flags into one verdict."""
    inventory_ready = inventory_file_ready and inventory_format_ready
    configured_category_count = sum(
        (
            inventory_configured,
            fleet_template_configured,
            media_endpoint_count > 0,
        )
    )
    ready_category_count = sum(
        (
            inventory_configured and inventory_ready,
            fleet_template_configured,
            media_endpoint_count > 0,
        )
    )
    data = {
        "configured_category_count": configured_category_count,
        "ready_category_count": ready_category_count,
        "inventory_configured": inventory_configured,
        "inventory_file_ready": inventory_file_ready,
        "inventory_format_ready": inventory_format_ready,
        "fleet_template_configured": fleet_template_configured,
        "media_endpoint_configured_count": media_endpoint_count,
        "media_endpoint_slot_count": 9,
        "network_probed": False,
        "redacted": True,
    }
    if not configured_category_count:
        return _result(
            "runtime_integrations",
            "skip",
            "optional inventory, fleet-template, and media endpoints are not configured",
            data=data,
        )
    if inventory_configured and not inventory_ready:
        return _result(
            "runtime_integrations",
            "warn",
            "runtime integration configuration is valid but the inventory file is "
            "unavailable or malformed",
            remediation=(
                "Point INFRA_INVENTORY_PATH at a readable, bounded YAML mapping, or "
                "unset it when infrastructure inventory ingestion is not used."
            ),
            data=data,
        )
    return _result(
        "runtime_integrations",
        "ok",
        f"{ready_category_count}/{configured_category_count} optional integration "
        "category(s) are configuration-ready",
        data=data,
    )


def _engine_endpoint_reachability(
    st: dict[str, Any], cfg: Any
) -> tuple[list, list, Any]:
    """``(endpoints, reachable, discovery_ready)`` for the resolved topology.

    A static group map is retained for migration/configuration audit only; it
    cannot satisfy the live placement authority. Probe whether authenticated
    ClusterMembers answers from a reachable seed for every multi-contact
    topology, even when legacy map data is present. The probe is an
    authenticated RPC (unlike the cheap raw-connect check), and the hermetic
    testing guard makes it fail closed without dialing.
    """
    from agent_utilities.knowledge_graph.core.placement_catalog import (
        discovery_reachable,
    )

    endpoints = st.get("endpoints", [])
    reachable = [e for e in endpoints if e.get("reachable")]
    discovery_ready = (
        discovery_reachable([e["endpoint"] for e in reachable], cfg)
        if len(endpoints) > 1
        else None
    )
    return endpoints, reachable, discovery_ready


def _engine_resource_limits(cfg: Any) -> dict[str, Any]:
    """The engine's configured request/response and extraction size bounds."""
    return {
        "request_bytes": getattr(cfg, "epistemic_graph_max_request_bytes", 0),
        "response_bytes": getattr(cfg, "epistemic_graph_max_response_bytes", 0),
        "msgpack_items": getattr(cfg, "epistemic_graph_max_msgpack_items", 0),
        "ast_files": getattr(cfg, "epistemic_graph_ast_max_files", 0),
        "ast_source_bytes": getattr(cfg, "epistemic_graph_ast_max_source_bytes", 0),
        "ast_total_bytes": getattr(cfg, "epistemic_graph_ast_max_total_bytes", 0),
        "modality_bundle_bytes": getattr(
            cfg, "epistemic_graph_modality_max_bundle_bytes", 0
        ),
        "modality_source_bytes": getattr(
            cfg, "epistemic_graph_modality_max_source_bytes", 0
        ),
        "sqlite_bytes": getattr(cfg, "epistemic_graph_sqlite_max_bytes", 0),
        "sqlite_rows": getattr(cfg, "epistemic_graph_sqlite_max_rows", 0),
    }


def _engine_redacted_status(
    cfg: Any,
    st: dict[str, Any],
    resolved: Any,
    encryption: dict[str, Any],
    reachable: list,
    discovery_ready: Any,
) -> dict[str, Any]:
    """Readiness counts only.

    Endpoint strings can contain hostnames, usernames, local socket paths, or
    customer-specific topology names. Doctor is frequently copied into issue
    reports and traces, so expose readiness counts only.
    """
    endpoints = st.get("endpoints", [])
    return {
        "resolved_mode": resolved.mode,
        "topology_mode": st.get("mode", "unknown"),
        "configured_endpoint_count": len(endpoints),
        "reachable_endpoint_count": len(reachable),
        "placement_group_mapping_count": len(
            getattr(cfg, "graph_raft_group_endpoints", {}) or {}
        ),
        "cluster_topology_discovery_ready": discovery_ready,
        "autostart_allowed": bool(resolved.autostart_allowed),
        "idle_shutdown_configured": bool(resolved.idle_shutdown_secs > 0),
        "durable_encryption": encryption,
        "runtime_directory_ref_count": sum(
            bool(value)
            for value in (
                getattr(cfg, "epistemic_graph_sqlite_transfer_root_ref", None),
                getattr(cfg, "epistemic_graph_backup_root_ref", None),
            )
        ),
        "resource_limits": _engine_resource_limits(cfg),
        "redacted": True,
    }


def _engine_runtime_directory_refs(cfg: Any) -> tuple[Any, ...]:
    """The configured local runtime-directory references, in check order."""
    return tuple(
        reference
        for reference in (
            getattr(cfg, "epistemic_graph_sqlite_transfer_root_ref", None),
            getattr(cfg, "epistemic_graph_backup_root_ref", None),
        )
        if reference
    )


def _rendered_directory_reference(resolver: Any, reference: Any) -> str:
    """Resolve one reference to a bounded, control-character-free directory string."""
    raw = resolver.resolve_ref(reference)
    rendered = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "")
    if (
        not rendered
        or len(rendered.encode("utf-8")) > 4_096
        or any(ord(character) < 32 for character in rendered)
    ):
        raise ValueError("invalid runtime directory")
    return rendered


def _assert_private_runtime_directory(resolver: Any, reference: Any) -> None:
    """Raise unless the reference names an existing, non-symlink, private directory."""
    import os
    from pathlib import Path

    candidate = Path(_rendered_directory_reference(resolver, reference))
    metadata = candidate.lstat()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("unsafe runtime directory")
    candidate.resolve(strict=True)
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("runtime directory is not private")


def _engine_runtime_directory_gate(
    resolved: Any, runtime_directory_refs: tuple[Any, ...], redacted_status: dict
) -> dict[str, Any] | None:
    """Every local runtime-directory reference must resolve to a private directory.

    Any failure -- unresolvable reference, symlink, non-directory, or group/other
    permissions -- marks the refs not ready and fails; it never reports ok.
    """
    if resolved.mode == "remote" or not runtime_directory_refs:
        return None
    try:
        from agent_utilities.security.secrets_client import create_secrets_client

        resolver = create_secrets_client()
        for reference in runtime_directory_refs:
            _assert_private_runtime_directory(resolver, reference)
        redacted_status["runtime_directory_refs_ready"] = True
    except Exception:  # noqa: BLE001 - diagnostics must not reveal paths/providers
        redacted_status["runtime_directory_refs_ready"] = False
        return _result(
            "engine",
            "fail",
            "an enabled engine file capability has an unavailable or unsafe runtime directory",
            remediation=(
                "Resolve each configured engine directory reference to an existing, "
                "non-symlink private directory; do not place host paths in AgentConfig."
            ),
            data=redacted_status,
        )
    return None


def _engine_remote_result(
    reachable: list,
    endpoints: list,
    runtime_directory_refs: tuple[Any, ...],
    redacted_status: dict[str, Any],
) -> dict[str, Any]:
    """Verdict for ``resolved mode=remote``; remote never autostarts a stand-in."""
    if not reachable:
        return _result(
            "engine",
            "fail",
            "configured remote engine is unreachable — "
            "remote mode never autostarts a local stand-in (fail-loud)",
            remediation="start the external engine (Docker/host) or fix GRAPH_SERVICE_ENDPOINTS",
            skill="agent-utilities-deployment",
            data=redacted_status,
        )
    if runtime_directory_refs:
        return _result(
            "engine",
            "warn",
            "remote engine reachable, but local runtime directory references are not applied remotely",
            remediation=(
                "Configure backup/SQLite roots in the remote engine deployment, "
                "or remove the local-only references."
            ),
            data=redacted_status,
        )
    return _result(
        "engine",
        "ok",
        f"remote engine reachable ({len(reachable)}/{len(endpoints)} "
        "endpoint(s)) — resolved mode=remote (deployed elsewhere)",
        data=redacted_status,
    )


def _engine_local_result(
    resolved: Any, reachable: list, endpoints: list, redacted_status: dict[str, Any]
) -> dict[str, Any]:
    """Verdict for a local engine: shared, autostart-on-demand, or unreachable."""
    if reachable:
        return _result(
            "engine",
            "ok",
            "engine reachable — resolved mode=shared "
            "(reusing the already-running local engine)",
            data=redacted_status,
        )
    # Nothing up locally — describe the autostart behaviour the resolver WILL
    # take on first use, including the idle-shutdown lifecycle.
    if resolved.autostart_allowed:
        life = (
            f"reference-counted (auto-stops {resolved.idle_shutdown_secs}s "
            "after the last client disconnects)"
            if resolved.idle_shutdown_secs > 0
            else "persistent (never auto-stops — runs like a local service)"
        )
        return _result(
            "engine",
            "warn",
            "no engine running yet — resolved mode=autostart: "
            f"a detached, supervised engine will be spawned on first use, {life}",
            remediation="no action needed (auto-provisions on demand); start eagerly with `graph-os-daemon` if preferred",
            skill="agent-utilities-deployment",
            data=redacted_status,
        )
    return _result(
        "engine",
        "fail",
        f"no epistemic-graph engine endpoint reachable ({len(endpoints)} configured) and autostart disabled",
        remediation="remove GRAPH_SERVICE_ENDPOINTS for the packaged local lifecycle, or start the configured external engine",
        skill="agent-utilities-deployment",
        data=redacted_status,
    )


def _check_engine() -> dict[str, Any]:
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.knowledge_graph.core.engine_resolver import resolve_engine
        from agent_utilities.knowledge_graph.core.graph_compute import (
            engine_encryption_readiness,
        )

        # Import gate only: _engine_endpoint_reachability re-imports it. Kept
        # here so an engine install missing the placement catalog still reports
        # `error` up front, exactly as it did before this check was split.
        from agent_utilities.knowledge_graph.core.placement_catalog import (  # noqa: F401
            discovery_reachable,
        )
        from agent_utilities.knowledge_graph.core.shard_topology import (
            default_graph_name,
            shard_topology_status,
        )

        cfg = AgentConfig()
        st = shard_topology_status(cfg, probe=True, timeout=0.5)
        resolved = resolve_engine(cfg, default_graph_name(cfg))
        encryption = engine_encryption_readiness(cfg, remote=resolved.mode == "remote")
    except Exception as exc:  # noqa: BLE001
        return _result(
            "engine",
            "error",
            f"shard topology probe failed ({type(exc).__name__})",
        )
    st["resolved_mode"] = resolved.mode
    endpoints, reachable, discovery_ready = _engine_endpoint_reachability(st, cfg)
    redacted_status = _engine_redacted_status(
        cfg, st, resolved, encryption, reachable, discovery_ready
    )

    if not encryption["ready"]:
        return _result(
            "engine",
            "fail",
            "local durable-engine encryption is not configuration-ready",
            remediation=(
                "Configure EPISTEMIC_GRAPH_ENCRYPTION_KEY_REF with an external "
                "runtime secret reference that resolves to bounded key material."
            ),
            data=redacted_status,
        )

    if len(endpoints) > 1 and not discovery_ready:
        return _result(
            "engine",
            "fail",
            "multiple coordinator contacts have no current engine-authoritative "
            "cluster-topology discovery (ClusterMembers) answer from any "
            "reachable contact",
            remediation=(
                "Ensure at least one configured GRAPH_SERVICE_ENDPOINTS seed is a "
                "live cluster member self-reported via NodeInfoUpsert (ADR-1 / "
                "W1.1) and answering the authenticated ClusterMembers RPC. "
                "GRAPH_RAFT_GROUP_ENDPOINTS is migration/audit data only and "
                "cannot replace discovery; clients never infer placement."
            ),
            data=redacted_status,
        )

    runtime_directory_refs = _engine_runtime_directory_refs(cfg)
    directory_failure = _engine_runtime_directory_gate(
        resolved, runtime_directory_refs, redacted_status
    )
    if directory_failure is not None:
        return directory_failure

    # CONCEPT:AU-OS.deployment.report-resolved-mode — report the RESOLVED mode (how this process reaches the
    # engine), not just transport reachability.
    if resolved.mode == "remote":
        return _engine_remote_result(
            reachable, endpoints, runtime_directory_refs, redacted_status
        )
    return _engine_local_result(resolved, reachable, endpoints, redacted_status)


def _check_engine_domains() -> dict[str, Any]:
    """BUG-013: a required capability module absent must fail readiness, not
    degrade silently past it.

    The 21 ``engine_<domain>`` tool families are declared REQUIRED
    (``feature=None``) in the canonical ``ToolSpec`` manifest — a real
    deployment always ships them (unlike the deployment-configurable
    ``quant``/``finance`` optional features). They are populated at runtime
    only when the ``epistemic_graph`` client package is importable
    (``engine_tools._discover_domains``); when it is not, that function
    already degrades gracefully rather than crashing the whole MCP process
    (so a ``tiny`` pre-bootstrap profile can still start) and logs a loud
    warning — but until this check existed, nothing turned that warning into
    a certification/readiness signal an operator or a gate could act on. A
    ``single-node-prod``/``enterprise`` deployment could boot, report itself
    healthy, and silently serve without ~21 required tool families (and
    their REST twins, and every ``code_context``/``graph_code`` KG-first
    capability that resolves through them) with no doctor check ever saying
    so — this is what let `tests/unit/test_gateway_mcp_parity.py`'s own
    parity checks report a legitimate skip (missing package, lean test lane)
    look, from a live-deployment reader's seat, indistinguishable from "this
    was never actually verified".

    ``tiny`` is the one profile documented
    (:mod:`graph_os.deployment.genesis_environments`) to run
    engine-less by design — warn there, fail everywhere else. This mirrors
    the exact ``tiny``-vs-else split :func:`_check_config` already
    established for the same profile distinction (durability findings are
    advisory on ``tiny``, a hard failure elsewhere).
    """
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.mcp.tools import engine_tools

        cfg = AgentConfig()
        profile = cfg.deployment_profile
        domain_count = len(engine_tools.ENGINE_DOMAINS)
    except Exception as exc:  # noqa: BLE001
        return _result(
            "engine_domains",
            "error",
            f"engine domain discovery check failed ({type(exc).__name__})",
        )
    data = {"profile": profile, "domain_count": domain_count, "redacted": True}
    if domain_count > 0:
        return _result(
            "engine_domains",
            "ok",
            f"{domain_count} engine_<domain> tool families registered",
            data=data,
        )
    status = "warn" if profile == "tiny" else "fail"
    return _result(
        "engine_domains",
        status,
        f"no engine_<domain> tool families registered for profile {profile!r} "
        "-- the 'epistemic_graph' client package is not importable",
        remediation=(
            "install the engine client (`pip install 'agent-utilities[graphos]'` "
            "or the `[serving]`/`[all]` extra, both of which pull it) so the "
            "required engine_<domain> tool families and their REST twins are "
            "actually served"
        ),
        skill="agent-utilities-deployment",
        data=data,
    )


def _check_engine_request_context() -> dict[str, Any]:
    """Report the fail-secure native-engine request-context posture."""

    data = {
        "verified_context_required": True,
        "legacy_protocol_available": False,
        "unauthenticated_transport_available": False,
    }
    return _result(
        "engine_request_context",
        "ok",
        "engine request context is current-only and requires verified identity",
        data=data,
    )


def _check_graph_authority() -> dict[str, Any]:
    """Verify that the live read/write authority is EpistemicGraphBackend.

    The doctor only inspects an already-active backend. It never constructs a
    connector, opens a local store, or exposes connection material.
    """
    try:
        from agent_utilities.knowledge_graph.backends import get_active_backend
        from agent_utilities.knowledge_graph.backends.brain_guarded_backend import (
            BrainGuardedBackend,
        )
        from agent_utilities.knowledge_graph.backends.epistemic_graph_backend import (
            EpistemicGraphBackend,
        )
        from agent_utilities.knowledge_graph.backends.fanout_backend import (
            FanOutBackend,
        )

        backend = get_active_backend()
        if backend is None:
            return _result(
                "graph_authority",
                "skip",
                "no graph authority active in this process (start GraphOS to evaluate)",
            )
        inner = backend.inner if isinstance(backend, BrainGuardedBackend) else backend
        authority = inner.authority if isinstance(inner, FanOutBackend) else inner
        if not isinstance(authority, EpistemicGraphBackend):
            return _result(
                "graph_authority",
                "fail",
                "active graph authority is not the required epistemic-graph engine",
                remediation=(
                    "restart GraphOS with current AgentConfig; declare external "
                    "databases only as source connectors or projection mirrors"
                ),
                skill="database-environment-setup",
                data={"authority_current": False},
            )
        hc = getattr(authority, "health_check", None)
        ok = hc() if callable(hc) else True
    except Exception as exc:  # noqa: BLE001
        return _result(
            "graph_authority",
            "warn",
            f"authority not evaluable ({type(exc).__name__})",
            remediation="restart GraphOS and validate the configured engine lifecycle",
            skill="database-environment-setup",
            data={"authority_current": False},
        )
    if ok:
        projection_count = len(getattr(inner, "_mirrors", {}))
        return _result(
            "graph_authority",
            "ok",
            "epistemic-graph authority reachable",
            data={
                "authority_current": True,
                "projection_count": projection_count,
            },
        )
    return _result(
        "graph_authority",
        "fail",
        "epistemic-graph authority health check failed",
        remediation="verify the managed epistemic-graph engine lifecycle",
        skill="database-environment-setup",
        data={"authority_current": True},
    )


def _check_secrets() -> dict[str, Any]:
    source_status: dict[str, Any] = {
        "state": "not_loaded",
        "present": False,
        "valid": True,
        "referenced_count": 0,
        "matched_count": 0,
        "projected_count": 0,
        "overridden_count": 0,
    }
    try:
        from agent_utilities.core.config import (
            AgentConfig,
            runtime_secret_source_status,
        )

        from graph_os.deployment.config_generator import _unresolved_secret_refs

        unresolved = _unresolved_secret_refs(AgentConfig())
        source_status = runtime_secret_source_status()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "secrets",
            "fail",
            f"secrets backend not evaluated ({type(exc).__name__})",
            remediation=(
                "repair the private runtime source or configured secret backend"
            ),
            skill="agent-utilities-deployment",
            data={
                "runtime_source": source_status,
                "redacted": True,
            },
        )
    data = {
        "runtime_source": source_status,
        "unresolved_count": len(unresolved),
        "redacted": True,
    }
    if not unresolved:
        return _result(
            "secrets",
            "ok",
            "no unresolved secret references",
            data=data,
        )
    return _result(
        "secrets",
        "fail",
        f"{len(unresolved)} secret reference(s) are unresolved",
        remediation="seed the values in your secrets backend",
        skill="secret-vault-manager",
        data=data,
    )


def _check_secrets_backend() -> dict[str, Any]:
    """Flag a runtime secret reference whose scheme names a backend that the
    *configured* ``SECRETS_BACKEND`` does not match.

    ``SecretsClient.resolve_ref`` resolves every ``vault://``/``secret://``
    reference through whichever backend is active — the scheme itself never
    selects a backend (CONCEPT:AU-OS.config.secrets-authentication). So a
    ``vault://`` reference silently resolves against the engine-backed
    ``__secrets__`` store instead of the named Vault/OpenBao instance when
    ``SECRETS_BACKEND != "vault"`` — and can even appear "resolved" if a
    same-named key happens to exist in the wrong store, which is why this is a
    *scheme/backend consistency* gate, independent of whether resolution
    happens to succeed (``_check_secrets`` above only catches total failure).
    """
    try:
        from agent_utilities.core.config import AgentConfig

        from graph_os.deployment.config_generator import (
            secret_reference_scheme_counts,
        )

        cfg = AgentConfig()
        backend = str(getattr(cfg, "secrets_backend", None) or "engine")
        counts = secret_reference_scheme_counts(cfg)
    except Exception as exc:  # noqa: BLE001
        return _result(
            "secrets_backend",
            "fail",
            f"secret reference scheme scan failed ({type(exc).__name__})",
            remediation="repair AgentConfig construction or the configured secrets backend",
            skill="agent-utilities-deployment",
            data={"redacted": True},
        )

    vault_refs = counts.get("vault", 0)
    engine_refs = counts.get("secret", 0)
    data = {
        "configured_backend": backend,
        "vault_scheme_ref_count": vault_refs,
        "secret_scheme_ref_count": engine_refs,
        "redacted": True,
    }

    if vault_refs and backend != "vault":
        return _result(
            "secrets_backend",
            "fail",
            f"{vault_refs} vault:// reference(s) configured but SECRETS_BACKEND={backend!r}; "
            "they resolve against the engine-backed store instead of Vault/OpenBao",
            remediation=(
                "Set SECRETS_BACKEND=vault so vault:// references resolve against the "
                "actual Vault/OpenBao instance, or replace them with secret:// / env:// "
                "references if the engine-backed store is genuinely intended."
            ),
            skill="secret-vault-manager",
            data=data,
        )

    if engine_refs and backend == "vault":
        return _result(
            "secrets_backend",
            "warn",
            f"{engine_refs} secret:// reference(s) configured while SECRETS_BACKEND='vault' "
            "(best-effort: secret:// does not itself guarantee the value lives in vault)",
            remediation=(
                "Confirm each secret:// reference's value genuinely lives in the "
                "configured vault mount, or rename it vault:// for an explicit, "
                "auditable scheme match."
            ),
            skill="secret-vault-manager",
            data=data,
        )

    return _result(
        "secrets_backend",
        "ok",
        f"secret reference schemes are consistent with SECRETS_BACKEND={backend!r}",
        data=data,
    )


def _check_auth() -> dict[str, Any]:
    from agent_utilities.core.config import config, setting
    from agent_utilities.security.request_identity import (
        local_process_authority_enabled,
    )

    if local_process_authority_enabled(config):
        return _result(
            "auth",
            "ok",
            "private ephemeral graph authority is ready for tiny stdio",
            data={
                "mode": "ephemeral_local",
                "network_transport_ready": False,
                "redacted": True,
            },
        )

    jwks = str(setting("AUTH_JWT_JWKS_URI", "") or "").strip()
    audience = str(setting("AUTH_JWT_AUDIENCE", "") or "").strip()
    policy_version = str(setting("KG_POLICY_VERSION", "") or "").strip()
    missing = [
        name
        for name, value in (
            ("AUTH_JWT_JWKS_URI", jwks),
            ("AUTH_JWT_AUDIENCE", audience),
            ("KG_POLICY_VERSION", policy_version),
        )
        if not value
    ]
    if not missing:
        # IdP-agnostic: any OIDC issuer's JWKS works. Name it for the report so
        # an operator on Okta isn't told they need Keycloak (CONCEPT:AU-OS.deployment.vault-first-routine-genesis genesis
        # IdP choice — keycloak deploy-if-absent OR an existing okta/other-oidc org).
        low = jwks.lower()
        idp = "Okta" if "okta" in low else ("Keycloak" if "keycloak" in low else "OIDC")
        return _result(
            "auth",
            "ok",
            f"verified graph authority configured ({idp}; audience + policy pinned)",
        )
    return _result(
        "auth",
        "fail",
        f"verified graph authority is incomplete ({len(missing)} setting(s) absent)",
        remediation=(
            "configure AUTH_JWT_JWKS_URI, AUTH_JWT_AUDIENCE, and KG_POLICY_VERSION; "
            "served graph operations fail closed until all three are present"
        ),
        skill="keycloak-client-onboarder",
        data={"missing_count": len(missing), "fail_closed": True},
    )


def _check_outbound_auth() -> dict[str, Any]:
    """Validate outbound MCP auth metadata without resolving credential material."""
    try:
        from agent_utilities.mcp.client_credentials import (
            outbound_auth_configuration_status,
        )

        status = outbound_auth_configuration_status()
    except Exception as exc:  # noqa: BLE001 - never report configured values
        return _result(
            "outbound_auth",
            "fail",
            f"outbound MCP authentication configuration is invalid ({type(exc).__name__})",
            remediation=(
                "Set MCP_CLIENT_AUTH and its canonical AgentConfig fields; keep "
                "credential material behind a runtime secret reference."
            ),
            data={"ready": False, "redacted": True},
        )
    mode = str(status["mode"])
    if mode == "none":
        return _result(
            "outbound_auth",
            "skip",
            "outbound MCP child authentication is disabled",
            data={"mode": mode, "ready": True, "redacted": True},
        )
    if bool(status["ready"]):
        return _result(
            "outbound_auth",
            "ok",
            f"outbound MCP child authentication is configured ({mode})",
            data={"mode": mode, "ready": True, "redacted": True},
        )
    missing = status.get("missing") or []
    invalid = status.get("invalid") or []
    if not isinstance(missing, list):
        missing = []
    if not isinstance(invalid, list):
        invalid = []
    return _result(
        "outbound_auth",
        "fail",
        "outbound MCP child authentication is incomplete",
        remediation=(
            "Configure OIDC_CLIENT_ID, OIDC_CLIENT_SECRET_REF, OIDC_AUDIENCE, "
            "and either OIDC_TOKEN_URL or OIDC_ISSUER in XDG AgentConfig."
        ),
        data={
            "mode": mode,
            "ready": False,
            "missing_count": len(missing),
            "invalid_count": len(invalid),
            "redacted": True,
        },
    )


def _graph_identity_readiness(token_ref: str, oauth2: Any) -> tuple[str, bool]:
    """``(mode, ready)`` for the single configured graph process identity source.

    Only the *reference* is resolved -- no token is minted and no resolved value
    ever leaves this function.
    """
    from agent_utilities.security.cli_secrets import resolve_runtime_secret_reference

    if token_ref:
        return "token_ref", bool(resolve_runtime_secret_reference(token_ref))
    assert oauth2 is not None
    secret_ref = str(oauth2.get("client_secret") or "")
    client_id = str(oauth2.get("client_id") or "")
    ready = bool(resolve_runtime_secret_reference(secret_ref))
    if client_id.startswith(("vault://", "env://", "secret://")):
        ready = ready and bool(resolve_runtime_secret_reference(client_id))
    return "oauth2_client_credentials", ready


def _check_graph_identity() -> dict[str, Any]:
    """Validate graph process identity without minting or exposing a token."""
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.security.request_identity import (
            local_process_authority_enabled,
        )

        cfg = AgentConfig()
        token_ref = str(cfg.kg_auth_token_ref or "").strip()
        oauth2 = cfg.kg_identity_oauth2
        if local_process_authority_enabled(cfg):
            return _result(
                "graph_identity",
                "ok",
                "private ephemeral graph process authority is ready",
                data={
                    "mode": "ephemeral_local",
                    "ready": True,
                    "redacted": True,
                },
            )
        if bool(token_ref) == bool(oauth2):
            return _result(
                "graph_identity",
                "fail",
                "graph process identity requires exactly one configured source",
                remediation=(
                    "Configure either KG_AUTH_TOKEN_REF or KG_IDENTITY_OAUTH2 "
                    "in XDG AgentConfig, never raw token material or both sources."
                ),
                data={"ready": False, "redacted": True},
            )
        mode, ready = _graph_identity_readiness(token_ref, oauth2)
        if not ready:
            return _result(
                "graph_identity",
                "fail",
                "graph process identity secret reference is unresolved",
                remediation="Seed the configured identity reference in the secrets backend.",
                data={"mode": mode, "ready": False, "redacted": True},
            )
        return _result(
            "graph_identity",
            "ok",
            f"graph process identity source is ready ({mode})",
            data={"mode": mode, "ready": True, "redacted": True},
        )
    except Exception as exc:  # noqa: BLE001 - doctor remains a redacted boundary
        return _result(
            "graph_identity",
            "fail",
            f"graph process identity configuration is invalid ({type(exc).__name__})",
            remediation=(
                "Validate AgentConfig and resolve its runtime secret references; "
                "no raw token or client secret is accepted."
            ),
            data={"ready": False, "redacted": True},
        )


def _check_mcp_fleet(live: bool = False) -> dict[str, Any]:
    try:
        import json
        from pathlib import Path

        from agent_utilities.core.workspace import get_mcp_config_path

        path = get_mcp_config_path()
        if not path or not Path(path).exists():
            return _result(
                "mcp_fleet",
                "skip",
                "no mcp_config.json found in workspace",
                remediation=(
                    "Create an optional fleet catalog in the AgentConfig XDG root, "
                    "or set MCP_CONFIG to an explicit external fleet catalog"
                ),
            )
        import importlib.util as _u

        spec = _u.find_spec("scripts.validate_mcp_config")
        if spec is None:
            return _result("mcp_fleet", "skip", "validate_mcp_config not importable")
        cfg = json.loads(Path(path).read_text())
        mod = importlib.import_module("scripts.validate_mcp_config")
        rep = mod.validate(
            cfg, mod.caddy_hosts() if hasattr(mod, "caddy_hosts") else set(), live=live
        )
    except Exception as exc:  # noqa: BLE001
        return _result(
            "mcp_fleet",
            "skip",
            f"fleet check skipped ({type(exc).__name__})",
        )
    safe_report = {
        "total": int(rep.get("total", 0)),
        "valid_count": len(rep.get("ok", [])),
        "invalid_count": len(rep.get("invalid", {})),
        "unreachable_count": len(rep.get("unreachable", {})),
        "missing_route_count": len(rep.get("missing_from_config", [])),
        "passed": bool(rep.get("passed", False)),
        "redacted": True,
    }
    if rep.get("passed"):
        return _result(
            "mcp_fleet",
            "ok",
            f"{safe_report['valid_count']} MCP server(s) valid",
            data=safe_report,
        )
    bad = {**rep.get("invalid", {}), **rep.get("unreachable", {})}
    return _result(
        "mcp_fleet",
        "warn",
        f"{len(bad)} MCP server(s) need attention",
        remediation="`python scripts/validate_mcp_config.py --live` for detail",
        data=safe_report,
    )


def _fleet_alias_kind(alias: str, reference: Any) -> str:
    """Classify one fleet alias as ``direct``, ``mapped``, or ``unresolved``.

    Any failure -- a missing projection, an unavailable reference, or control
    characters in either -- is ``unresolved``: the alias never counts as ready.
    """
    from agent_utilities.core.config import setting
    from agent_utilities.security.cli_secrets import resolve_runtime_secret_reference

    try:
        direct = setting(alias)
        if direct not in (None, ""):
            if any(character in str(direct) for character in "\x00\r\n"):
                raise ValueError("invalid direct runtime material")
            return "direct"
        resolved = resolve_runtime_secret_reference(reference)
        if resolved in (None, "") or any(
            character in str(resolved) for character in "\x00\r\n"
        ):
            raise ValueError("unavailable runtime reference")
        return "mapped"
    except Exception:  # noqa: BLE001 - aliases and references stay redacted
        return "unresolved"


def _check_mcp_fleet_secrets() -> dict[str, Any]:
    """Validate neutral fleet alias resolution without disclosing alias metadata."""

    data = {
        "configured_alias_count": 0,
        "direct_alias_count": 0,
        "mapped_alias_count": 0,
        "unresolved_alias_count": 0,
        "redacted": True,
    }
    try:
        from agent_utilities.core.config import AgentConfig

        mappings = AgentConfig().mcp_fleet_secret_refs
        if not isinstance(mappings, dict) or len(mappings) > 512:
            raise ValueError("invalid fleet secret alias mapping")
        data["configured_alias_count"] = len(mappings)
        for alias, reference in mappings.items():
            data[f"{_fleet_alias_kind(alias, reference)}_alias_count"] += 1
    except Exception as exc:  # noqa: BLE001 - doctor remains a redacted boundary
        return _result(
            "mcp_fleet_secrets",
            "fail",
            f"MCP fleet secret alias configuration is invalid ({type(exc).__name__})",
            remediation=(
                "Configure MCP_FLEET_SECRET_REFS as neutral aliases mapped only "
                "to env://, vault://, or secret:// runtime references."
            ),
            data=data,
        )
    if data["unresolved_alias_count"]:
        return _result(
            "mcp_fleet_secrets",
            "fail",
            "one or more MCP fleet secret aliases cannot be resolved",
            remediation=(
                "Project the direct runtime alias or repair its configured "
                "runtime secret reference."
            ),
            data=data,
        )
    return _result(
        "mcp_fleet_secrets",
        "ok",
        "MCP fleet secret alias resolution is ready",
        data=data,
    )


def _check_openai_catalog(live: bool = False) -> dict[str, Any]:
    """Verify configured OpenAI models against the live catalogue (CONCEPT:AU-ORCH.adapter.openai-catalog-verification).

    Static (default): reports which credential tier resolved (secret_ref/env/file/
    none) and how many OpenAI-provider models are configured in the active model
    registry, without a network call. ``live=True`` additionally calls
    ``verify_openai_model`` for each configured model id, so a wrong/renamed/
    inaccessible model id is caught before a deployment relies on it rather than
    assumed to exist. Never reports the resolved API key value.
    """
    try:
        from agent_utilities.core.credentials import CredentialResolver
        from agent_utilities.models.model_registry import load_active_registry

        creds = CredentialResolver().resolve("openai")
        registry = load_active_registry()
        openai_models = [m for m in registry.models if m.provider == "openai"]
        data: dict[str, Any] = {
            "credential_source": creds.source,
            "credential_configured": bool(creds.api_key),
            "configured_model_count": len(openai_models),
            "live_probed": False,
            "redacted": True,
        }
    except Exception as exc:  # noqa: BLE001 - doctor is a redaction boundary
        return _result(
            "openai_catalog",
            "error",
            f"openai catalog check failed ({type(exc).__name__})",
            data={"redacted": True},
        )

    if not openai_models:
        return _result(
            "openai_catalog",
            "skip",
            "no OpenAI-provider models are configured in the active model registry",
            data=data,
        )
    if not creds.api_key:
        return _result(
            "openai_catalog",
            "fail",
            "OpenAI models are configured but no API key/secret reference is available",
            remediation=(
                "Set OPENAI_API_KEY_REF to an env://, vault://, or secret:// "
                "reference (preferred) or the literal OPENAI_API_KEY."
            ),
            data=data,
        )
    if not live:
        return _result(
            "openai_catalog",
            "ok",
            f"{len(openai_models)} OpenAI model(s) configured with a resolvable "
            "credential (pass live=True to verify against the live catalogue)",
            data=data,
        )

    return _openai_catalog_live_result(openai_models, creds, data)


def _openai_catalog_live_result(
    openai_models: list[Any], creds: Any, data: dict[str, Any]
) -> dict[str, Any]:
    """Verify every configured OpenAI model id against the live catalogue.

    ``data`` is mutated with the probe outcome so the returned result always
    carries what was actually verified -- a probe that cannot complete raises
    into the caller rather than reporting ok.
    """
    from agent_utilities.core.openai_catalog import verify_openai_model

    async def _verify_all() -> list[Any]:
        return [
            await verify_openai_model(
                m.model_id,
                api_key=creds.api_key,
                base_url=m.base_url or creds.base_url,
            )
            for m in openai_models
        ]

    verifications = _run_async_doctor_probe(_verify_all)
    data["live_probed"] = True
    verified = sum(1 for v in verifications if v.exists)
    data["verified_count"] = verified
    data["unverified_model_ids"] = [v.model_id for v in verifications if not v.exists]
    if verified < len(openai_models):
        return _result(
            "openai_catalog",
            "fail",
            f"{len(openai_models) - verified} of {len(openai_models)} configured "
            "OpenAI model(s) were not found in the live catalogue",
            remediation=(
                "Correct the model_id or confirm the credential has access to it."
            ),
            data=data,
        )
    return _result(
        "openai_catalog",
        "ok",
        f"all {len(openai_models)} configured OpenAI model(s) verified against "
        "the live catalogue",
        data=data,
    )


def _check_provider_profiles() -> dict[str, Any]:
    """Resolve enabled provider profiles without exposing deployment metadata."""

    data = {
        "configured_count": 0,
        "enabled_count": 0,
        "disabled_count": 0,
        "ready_count": 0,
        "invalid_count": 0,
        "redacted": True,
    }
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.core.provider_runtime import (
            prepare_provider_runtime_child_environment,
        )

        cfg = AgentConfig()
        profiles = cfg.provider_configs
        if not isinstance(profiles, dict) or len(profiles) > 256:
            raise ValueError("provider profile mapping is invalid")
        data["configured_count"] = len(profiles)
        for profile_name, profile in profiles.items():
            if not profile.enabled:
                data["disabled_count"] += 1
                continue
            data["enabled_count"] += 1
            try:
                prepared = prepare_provider_runtime_child_environment(
                    profile_name, config=cfg
                )
                prepared.close()
                data["ready_count"] += 1
            except Exception:  # noqa: BLE001 - deployment details stay redacted
                data["invalid_count"] += 1
    except Exception as exc:  # noqa: BLE001 - doctor is a redaction boundary
        return _result(
            "provider_profiles",
            "fail",
            f"provider runtime profile configuration is invalid ({type(exc).__name__})",
            remediation=(
                "Configure PROVIDER_CONFIGS with neutral profile names, runtime "
                "references, and explicit TLS profile selectors."
            ),
            data=data,
        )
    if not data["configured_count"]:
        return _result(
            "provider_profiles",
            "skip",
            "no external provider runtime profiles are configured",
            data=data,
        )
    if data["invalid_count"]:
        return _result(
            "provider_profiles",
            "fail",
            "one or more enabled provider runtime profiles are unavailable",
            remediation=(
                "Repair the referenced endpoint, credential, selector, or TLS "
                "profile in the deployment-owned configuration."
            ),
            data=data,
        )
    return _result(
        "provider_profiles",
        "ok",
        "enabled provider runtime profiles are ready",
        data=data,
    )


def _check_hooks() -> dict[str, Any]:
    try:
        from agent_utilities.ecosystem.hook_installer import HookInstaller

        rep = HookInstaller().doctor()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "hooks", "skip", f"hook doctor unavailable ({type(exc).__name__})"
        )
    installed = [k for k, v in rep.items() if v.get("status") == "healthy"]
    stale = [k for k, v in rep.items() if v.get("status") == "stale"]
    if stale:
        return _result(
            "hooks",
            "warn",
            f"{len(stale)} stale hook(s): {stale}",
            remediation="re-install hooks (`graph_configure action=install_hooks`)",
            auto_fixable=True,
            data=rep,
        )
    return _result("hooks", "ok", f"{len(installed)} agent hook(s) healthy", data=rep)


def _probe_native_optimizer_live() -> dict[str, Any]:
    """Submit one content-free ProgramOptimize job to the active authority."""
    from agent_utilities.harness.optimization_backend import (
        OptimizationRequest,
        try_native_optimization,
    )
    from agent_utilities.knowledge_graph.core.graph_compute import GraphComputeEngine

    engine = GraphComputeEngine.get_active()
    if engine is None:
        return {
            "live_probed": True,
            "operational": False,
            "error_code": "engine_authority_inactive",
            "privacy_safe_payload": True,
        }
    request = OptimizationRequest(
        target="diagnostic",
        objective="native-capability-probe",
        data={
            "examples": [
                {
                    "task": "synthetic-capability-probe",
                    "response": "synthetic-capability-result",
                    "success": True,
                }
            ]
        },
    )
    attempt = try_native_optimization(engine, request)
    out: dict[str, Any] = {
        "live_probed": True,
        "operational": attempt.disposition == "completed",
        "privacy_safe_payload": True,
    }
    if attempt.disposition != "completed":
        out["error_code"] = attempt.error_code or f"native_{attempt.disposition}"
    return out


def _check_native_optimizer(live: bool = False) -> dict[str, Any]:
    """Report installed surface separately from a live ProgramOptimize proof."""
    try:
        from agent_utilities.core.config import AgentConfig
        from agent_utilities.knowledge_graph.core.graph_compute import (
            GraphComputeEngine,
        )

        cfg = AgentConfig()
        enabled = bool(cfg.kg_optimization_enabled)
        surface_available = callable(
            getattr(GraphComputeEngine, "optimize_program", None)
        )
        data = {
            "enabled": enabled,
            "native_surface_available": surface_available,
            "live_probed": False,
            "operational": None,
            "privacy_safe_payload": True,
        }
        if not enabled:
            return _result(
                "native_optimizer",
                "skip",
                "native program optimization is disabled",
                data=data,
            )
        if not surface_available:
            return _result(
                "native_optimizer",
                "fail",
                "the native ProgramOptimize surface is unavailable",
                remediation="Install the unified agent-utilities engine distribution.",
                data=data,
            )
        if not live:
            return _result(
                "native_optimizer",
                "ok",
                "the ProgramOptimize surface is installed; live proof was not requested",
                data=data,
            )
        live_data = _probe_native_optimizer_live()
        data.update(live_data)
        if not data["operational"]:
            return _result(
                "native_optimizer",
                "fail",
                "the active engine did not complete the ProgramOptimize capability probe",
                remediation=(
                    "Run the live doctor inside GraphOS after its engine authority is active, "
                    "then inspect the engine health check if ProgramOptimize still fails."
                ),
                data=data,
            )
        return _result(
            "native_optimizer",
            "ok",
            "the active engine completed a governed ProgramOptimize job",
            data=data,
        )
    except Exception as exc:  # noqa: BLE001 - never expose engine response material
        return _result(
            "native_optimizer",
            "error",
            f"native optimizer readiness check failed ({type(exc).__name__})",
            data={"redacted": True, "live_probed": live},
        )


def _graph_connection_declarations(
    cfg: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(external, kg)`` connection declarations, normalised to plain dicts."""
    external_declarations: list[dict[str, Any]] = []
    for declared in cfg.external_graph_connectors or []:
        value = (
            declared.model_dump(exclude_none=True, exclude_defaults=True)
            if hasattr(declared, "model_dump")
            else dict(declared)
        )
        value["role"] = "read"
        external_declarations.append(value)
    kg_declarations = [dict(value) for value in (cfg.kg_connections or [])]
    return external_declarations, kg_declarations


def _invalid_declaration_count(declarations: list[dict[str, Any]]) -> int:
    """How many declarations fail the persistable spec or carry no usable name."""
    from agent_utilities.knowledge_graph.core.connection_registry import (
        validate_persistable_connection_spec,
    )

    invalid = 0
    for declaration in declarations:
        try:
            validate_persistable_connection_spec(declaration)
            if not str(declaration.get("name") or "").strip():
                raise ValueError("connection declaration has no name")
        except Exception:  # noqa: BLE001 - expose only aggregate counts
            invalid += 1
    return invalid


def _declared_names(values: list[dict[str, Any]]) -> list[str]:
    """Each declaration's non-empty stripped ``name``; duplicates are kept."""
    return [
        str(value.get("name") or "").strip()
        for value in values
        if str(value.get("name") or "").strip()
    ]


def _graph_connection_inventory(cfg: Any) -> SimpleNamespace:
    """Reconcile the declared connections against the live registry.

    ``KG_CONNECTIONS`` intentionally overrides an ``EXTERNAL_GRAPH_CONNECTORS``
    declaration with the same alias. Duplicates within either source are invalid
    and cannot be hidden by that precedence rule.
    """
    from graph_os.mcp_server.bootstrap import get_connection_registry

    external_declarations, kg_declarations = _graph_connection_declarations(cfg)
    invalid_declaration_count = _invalid_declaration_count(
        [*external_declarations, *kg_declarations]
    )
    external_names = _declared_names(external_declarations)
    kg_names = _declared_names(kg_declarations)
    effective_names = set(external_names) | set(kg_names)
    registry = get_connection_registry()
    conns = [
        connection
        for connection in registry.status().get("connections", [])
        if connection.get("name") != "default"
    ]
    registered_names = set(_declared_names(conns))
    return SimpleNamespace(
        registry=registry,
        conns=conns,
        registered_names=registered_names,
        effective_names=effective_names,
        invalid_declaration_count=invalid_declaration_count,
        duplicate_declaration_count=(
            len(external_names)
            - len(set(external_names))
            + len(kg_names)
            - len(set(kg_names))
        ),
        missing_declaration_count=len(effective_names - registered_names),
    )


def _graph_connection_probe_counts(
    registry: Any, registered_names: set[str], *, live: bool
) -> tuple[int, int]:
    """``(ready, probe_failed)``; a probe that raises counts as failed, never ready."""
    if not live:
        return 0, 0
    ready_count = 0
    probe_failed_count = 0
    for name in sorted(registered_names):
        try:
            if registry.probe(name):
                ready_count += 1
            else:
                probe_failed_count += 1
        except Exception:  # noqa: BLE001 - never expose connector details
            probe_failed_count += 1
    return ready_count, probe_failed_count


def _stalled_mirror_count() -> int:
    """Stalled fan-out mirrors; best-effort, an unavailable backend reports 0."""
    try:
        from agent_utilities.knowledge_graph.backends import get_active_backend
        from agent_utilities.knowledge_graph.backends.fanout_backend import (
            FanOutBackend,
        )

        backend = get_active_backend()
        cand = getattr(backend, "inner", backend)
        if not isinstance(cand, FanOutBackend):
            return 0
        mirrors = cand.durability_stats().get("mirrors") or {}
        return sum(bool(state.get("stalled")) for state in mirrors.values())
    except Exception:  # noqa: BLE001 — mirror stats are best-effort
        return 0


def _graph_connections_data(
    inventory: SimpleNamespace,
    ready_count: int,
    probe_failed_count: int,
    stalled_count: int,
    *,
    live: bool,
) -> dict[str, Any]:
    """Aggregate, redacted connection metadata -- never an alias or endpoint."""
    by_role: dict[str, int] = {}
    for connection in inventory.conns:
        role = str(connection.get("role") or "read")
        by_role[role] = by_role.get(role, 0) + 1
    return {
        "configured_count": len(inventory.effective_names),
        "registered_count": len(inventory.registered_names),
        "ready_count": ready_count,
        "probe_failed_count": probe_failed_count,
        "invalid_declaration_count": inventory.invalid_declaration_count,
        "duplicate_declaration_count": inventory.duplicate_declaration_count,
        "missing_declaration_count": inventory.missing_declaration_count,
        "stalled_mirror_count": stalled_count,
        "roles": by_role,
        "redacted": True,
        "live_probed": live,
    }


def _graph_connections_verdict(
    inventory: SimpleNamespace,
    data: dict[str, Any],
    probe_failed_count: int,
    stalled_count: int,
    *,
    live: bool,
) -> dict[str, Any]:
    """Any declaration or probe failure is a fail; stalled mirrors are a warn."""
    registered = len(inventory.registered_names)
    configuration_failures = (
        inventory.invalid_declaration_count
        + inventory.duplicate_declaration_count
        + inventory.missing_declaration_count
    )
    if configuration_failures or probe_failed_count:
        return _result(
            "graph_connections",
            "fail",
            (
                f"{registered} external connection(s); "
                f"{configuration_failures} declaration failure(s), "
                f"{probe_failed_count} runtime probe failure(s)"
            ),
            remediation=(
                "Repair the referenced connection, authentication, and TLS profiles, "
                "then rerun the live doctor."
            ),
            skill="database-environment-setup",
            data=data,
        )
    if stalled_count:
        return _result(
            "graph_connections",
            "warn",
            f"{registered} connection(s); {stalled_count} stalled mirror(s)",
            remediation="`graph_configure action=reconcile` and check the mirror backend",
            skill="database-environment-setup",
            data=data,
        )
    if not inventory.registered_names:
        detail = "no external connections registered"
    elif live:
        detail = f"{data['ready_count']}/{registered} external connection(s) ready"
    else:
        detail = (
            f"{registered} external connection declaration(s) valid; "
            "live proof not requested"
        )
    return _result("graph_connections", "ok", detail, data=data)


def _check_graph_connections(live: bool = False) -> dict[str, Any]:
    """Validate graph declarations and optionally prove their native read paths.

    Every declaration, including sources declared only in ``KG_CONNECTIONS``, is
    checked on every run. Network probes run only for an explicit live doctor.
    Public output is aggregate metadata: aliases, endpoints, refs, identities,
    source rows, and exception details never cross the doctor boundary.
    """
    try:
        from agent_utilities.core.config import AgentConfig

        inventory = _graph_connection_inventory(AgentConfig())
    except Exception:  # noqa: BLE001 - never expose deployment details
        return _result(
            "graph_connections",
            "fail",
            "graph connection registry is invalid",
            remediation=(
                "Repair KG_CONNECTIONS and external graph declarations; keep all "
                "transport, auth, and TLS material behind runtime references."
            ),
            data={"ready": False, "redacted": True, "live_probed": live},
        )

    ready_count, probe_failed_count = _graph_connection_probe_counts(
        inventory.registry, inventory.registered_names, live=live
    )
    stalled_count = _stalled_mirror_count()
    data = _graph_connections_data(
        inventory, ready_count, probe_failed_count, stalled_count, live=live
    )
    return _graph_connections_verdict(
        inventory, data, probe_failed_count, stalled_count, live=live
    )


def _check_venv_drift() -> dict[str, Any]:
    """Is the shared uv-workspace venv still what its lock says it should be?

    CONCEPT:AU-OS.host.venv-drift-detector

    This check exists because the shared development environment silently sat
    ten days behind its own lock: one import failed, an entire test module
    stopped collecting, and thirteen real defects were invisible until somebody
    happened to look.  Nothing was checking, so nothing was known.  Running it
    here means every ``agent-utilities doctor`` answers the question.

    Deployments have no uv workspace above them (the runtime image installs
    wheels), so an absent workspace is a ``skip``, not a failure.
    """

    try:
        from graph_os.deployment.venv_sync import (
            Workspace,
            WorkspaceNotFoundError,
            detect_drift,
        )
    except ImportError as exc:
        return _result("venv_drift", "error", f"venv_sync is not importable: {exc}")

    try:
        workspace = Workspace.discover()
    except WorkspaceNotFoundError:
        return _result(
            "venv_drift",
            "skip",
            "no uv workspace above this checkout (normal for a deployed runtime)",
        )
    except Exception as exc:  # noqa: BLE001 - a probe must never crash the doctor
        return _result("venv_drift", "error", f"workspace discovery failed: {exc}")

    try:
        report = detect_drift(workspace)
    except Exception as exc:  # noqa: BLE001 - a probe must never crash the doctor
        return _result("venv_drift", "error", f"drift detection failed: {exc}")

    status = {"ok": "ok", "warn": "warn", "fail": "fail"}[report.status]
    return _result(
        "venv_drift",
        status,
        report.summary,
        remediation=(
            None
            if status == "ok"
            else (
                "`agent-utilities-venv status` for detail, then "
                "`agent-utilities-venv sync` (safe form, guardrailed) or "
                "`agent-utilities-venv relock` when a manifest moved."
            )
        ),
        data=report.as_dict(),
    )


def _check_warm_fork() -> dict[str, Any]:
    """Report available confined sandbox rungs and pooled VM parents."""
    rungs: dict[str, dict[str, Any]] = {}
    try:
        from agent_utilities.rlm.sandboxes.registry import default_sandboxes

        for b in default_sandboxes():
            caps = b.capabilities
            try:
                available = bool(b.is_available())
            except Exception:  # noqa: BLE001 - a probe must never crash the doctor
                available = False
            rungs[b.name] = {
                "available": available,
                "isolated": caps.isolated,
                "warm_fork": caps.warm_fork,
                "rank": caps.preference_rank,
            }
    except Exception as exc:  # noqa: BLE001
        return _result(
            "warm_fork",
            "error",
            f"could not enumerate sandbox rungs ({type(exc).__name__})",
        )

    try:
        from agent_utilities.runtime.warm_registry import WarmParentRegistry

        pool = WarmParentRegistry.get().stats()
    except Exception:  # noqa: BLE001
        pool = {}

    warm_rungs = sorted(
        n for n, r in rungs.items() if r["warm_fork"] and r["available"]
    )
    data = {"rungs": rungs, "warm_rungs": warm_rungs, "pool": pool}
    if warm_rungs:
        return _result(
            "warm_fork",
            "ok",
            f"native warm-fork available via: {', '.join(warm_rungs)}",
            data=data,
        )
    return _result(
        "warm_fork",
        "warn",
        "no warm-fork rung available — sandboxes will cold-start every run",
        remediation=(
            "Install the sandbox extra plus a WASI payload, configure an immutable "
            "container image, or connect a governed microVM controller."
        ),
        data=data,
    )


def _check_a2a_persistence() -> dict[str, Any]:
    """Validate the unary facade's canonical WorkItem/dispatch dependencies."""

    try:
        from agent_utilities.knowledge_graph.core.work_durability import (
            cancel_work_item,
            get_work_item,
            submit_work_item_atomic,
        )
        from agent_utilities.orchestration.agent_dispatch import enqueue_agent_turn

        from graph_os.a2a import OrchestratorA2ARouter, WorkItemA2AAuthority
    except Exception as exc:  # noqa: BLE001 - doctor reports no configuration values
        return _result(
            "a2a_persistence",
            "fail",
            f"canonical A2A authority is unavailable ({type(exc).__name__})",
            remediation=(
                "Install compatible graph-os and agent-utilities artifacts with "
                "WorkItem durability and signed agent dispatch."
            ),
            data={"ready": False, "redacted": True},
        )

    callables = (
        submit_work_item_atomic,
        get_work_item,
        cancel_work_item,
        enqueue_agent_turn,
    )
    data = {
        "canonical_work_item_authority": all(
            callable(value) for value in callables[:3]
        ),
        "canonical_dispatch_authority": callable(enqueue_agent_turn),
        "adapter_count": 2
        if WorkItemA2AAuthority is not None and OrchestratorA2ARouter is not None
        else 0,
        "redacted": True,
    }
    if not all(data.values()):
        return _result(
            "a2a_persistence",
            "fail",
            "canonical A2A authority contract is incomplete",
            remediation=(
                "Install compatible WorkItem durability and signed agent dispatch "
                "artifacts; do not configure a second A2A task store."
            ),
            data=data,
        )
    return _result(
        "a2a_persistence",
        "ok",
        "A2A reuses canonical WorkItem durability and signed agent dispatch",
        data=data,
    )


# Registry: name -> callable. Order is the report order.
CHECKS: dict[str, Callable[..., dict[str, Any]]] = {
    "python_env": _check_python_env,
    "mcp_sdk_floor": _check_mcp_sdk_floor,
    "config": _check_config,
    "evolution_staging": _check_evolution_staging,
    "execution_security": _check_execution_security,
    "permission_governance": _check_permission_governance,
    "transport_security": _check_transport_security,
    "google_workspace_oauth": _check_google_workspace_oauth,
    "source_egress": _check_source_egress,
    "eunomia": _check_eunomia,
    "runtime_integrations": _check_runtime_integrations,
    "provider_profiles": _check_provider_profiles,
    "openai_catalog": _check_openai_catalog,
    "workspace_config": _check_workspace_config,
    "engine_request_context": _check_engine_request_context,
    "engine": _check_engine,
    "engine_domains": _check_engine_domains,
    "graph_authority": _check_graph_authority,
    "graph_connections": _check_graph_connections,
    "secrets": _check_secrets,
    "secrets_backend": _check_secrets_backend,
    "auth": _check_auth,
    "outbound_auth": _check_outbound_auth,
    "skill_certification": _check_skill_certification,
    "production_certification": _check_production_certification,
    "graph_identity": _check_graph_identity,
    "mcp_fleet_secrets": _check_mcp_fleet_secrets,
    "mcp_fleet": _check_mcp_fleet,
    "hooks": _check_hooks,
    "observability": _check_observability,
    "langfuse": _check_langfuse,
    "native_optimizer": _check_native_optimizer,
    "a2a_persistence": _check_a2a_persistence,
    "bus": _check_bus,
    "skills": _check_skills,
    "unified_install": _check_unified_install,
    "venv_drift": _check_venv_drift,
    "warm_fork": _check_warm_fork,
    "kafka": _check_kafka,
    "fuseki": _check_fuseki,
    "seaweedfs_s3": _check_seaweedfs_s3,
    "lakekeeper": _check_lakekeeper,
    "lakekeeper_db": _check_lakekeeper_db,
    "trino": _check_trino,
    "spark_runner": _check_spark_runner,
}

# Checks that accept a `live=` kwarg and prove real reachability when the doctor
# is run with `--live` -- shared between `run_doctor`'s dispatch and
# `interactive_apply`'s post-apply re-run so the two never drift apart.
_LIVE_CHECK_NAMES = frozenset(
    {
        "graph_connections",
        "mcp_fleet",
        "langfuse",
        "native_optimizer",
        "openai_catalog",
        "kafka",
        "fuseki",
        "seaweedfs_s3",
        "lakekeeper",
        "lakekeeper_db",
        "trino",
        "spark_runner",
    }
)


def _auto_fix(name: str) -> dict[str, Any]:
    """Run a conservative, idempotent remediation for an auto-fixable check."""
    if name == "hooks":
        try:
            from agent_utilities.ecosystem.hook_installer import HookInstaller

            inst = HookInstaller()
            inst.install()
            return {
                "fixed": name,
                "result": "re-installed hooks",
                "errors": inst.errors,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "fixed": name,
                "error": f"auto-fix failed ({type(exc).__name__})",
            }
    return {"fixed": name, "result": "no auto-fix available"}


def _selection_is_invalid(only: Any) -> bool:
    """Reject anything but a non-empty, duplicate-free list of registered names."""
    return (
        not isinstance(only, list)
        or not only
        or len(only) > len(CHECKS)
        or any(not isinstance(name, str) or name not in CHECKS for name in only)
        or len(set(only)) != len(only)
    )


def _unhealthy_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    """A report that ran no check: every result is an error, so it fails closed."""
    return {
        "status": "unhealthy",
        "counts": {"error": len(results)},
        "checks": results,
        "fixes": [],
        "summary": _summarize("unhealthy", results),
    }


def _load_runtime_config(names: list[str]) -> list[dict[str, Any]] | None:
    """Load the deployment AgentConfig; on failure, one error result per check.

    Every runtime entry point consumes the same XDG AgentConfig document. A
    doctor launched directly from its console script must do that too; without
    this load, checks that instantiate ``AgentConfig`` would silently inspect
    package defaults instead of the deployment GraphOS actually uses. Returning
    results (rather than proceeding) keeps the sweep fail-closed: no check may
    report ok against defaults the deployment does not use.
    """
    try:
        from agent_utilities.core.config import load_config

        load_config()
    except Exception as exc:  # noqa: BLE001 - source details may be sensitive
        return [
            _result(
                name,
                "error",
                f"configuration load failed ({type(exc).__name__})",
                remediation=(
                    "Repair the private AgentConfig source, then rerun the doctor; "
                    "configuration values are intentionally not reported."
                ),
                data={"redacted": True},
            )
            for name in names
        ]
    return None


def _run_checks(names: list[str], *, live: bool) -> list[dict[str, Any]]:
    """Run each selected check; a check that raises becomes an error result."""
    results: list[dict[str, Any]] = []
    for name in names:
        fn = CHECKS.get(name)
        if fn is None:
            continue
        try:
            res = fn(live=live) if name in _LIVE_CHECK_NAMES else fn()
        except Exception as exc:  # noqa: BLE001 — a check must never crash the doctor
            res = _result(name, "error", f"check raised ({type(exc).__name__})")
        results.append(res)
    return results


def _apply_auto_fixes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Auto-remediate the ``auto_fixable`` not-ok checks and re-run each in place."""
    fixes: list[dict[str, Any]] = []
    for res in results:
        if res["status"] in ("warn", "fail") and res.get("auto_fixable"):
            fixes.append(_auto_fix(res["name"]))
            try:
                res.update(CHECKS[res["name"]]())  # re-run after fix
            except Exception:  # noqa: BLE001 - auto-fix report remains failed  # nosec B110
                pass
    return fixes


def _doctor_report(
    results: list[dict[str, Any]], fixes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Aggregate the per-check results into the overall verdict (worst wins)."""
    worst = max((_RANK[r["status"]] for r in results), default=0)
    overall = {0: "healthy", 1: "warnings", 2: "unhealthy"}[worst]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "status": overall,
        "counts": counts,
        "checks": results,
        "fixes": fixes,
        "summary": _summarize(overall, results),
    }


def run_doctor(
    only: list[str] | None = None, *, fix: bool = False, live: bool = False
) -> dict[str, Any]:
    """Run the health sweep and return a structured report.

    Args:
        only: restrict to these check names (default: all).
        fix: run conservative auto-remediations for ``auto_fixable`` checks, then
            re-run those checks.
        live: prove network and engine capabilities for live-aware checks.
    """
    if only is None:
        names = list(CHECKS)
    elif _selection_is_invalid(only):
        return _unhealthy_report(
            [
                _result(
                    "selection",
                    "error",
                    "doctor check selection is invalid",
                    remediation="select one or more registered doctor checks",
                    data={"redacted": True},
                )
            ]
        )
    else:
        names = only
    load_failures = _load_runtime_config(names)
    if load_failures is not None:
        return _unhealthy_report(load_failures)
    results = _run_checks(names, live=live)
    fixes = _apply_auto_fixes(results) if fix else []
    return _doctor_report(results, fixes)


def _summarize(overall: str, results: list[dict[str, Any]]) -> str:
    bad = [r["name"] for r in results if r["status"] in ("warn", "fail", "error")]
    if overall == "healthy":
        return "All checks passed."
    return f"{overall}: attend to {bad}. Each failing check lists a remediation/skill."


def _format_prescription(check: dict[str, Any]) -> str:
    """Render one not-ok check's plan for operator review before any confirmation prompt."""
    prescription = check.get("prescription") or {}
    lines = [
        f"  service: {check['name']} ({check['status']})",
        f"  detail : {check['detail']}",
    ]
    manifest_path = prescription.get("manifest_path")
    if manifest_path:
        lines.append(f"  manifest: {manifest_path}")
    config_keys = prescription.get("config_keys") or {}
    if config_keys:
        lines.append("  config keys:")
        for key, value in config_keys.items():
            lines.append(f"    {key} = {value}")
    gotcha = prescription.get("gotcha")
    if gotcha:
        lines.append(f"  known gotcha: {gotcha}")
    scaling = prescription.get("scaling") or {}
    if scaling:
        if scaling.get("supported"):
            lines.append("  scaling: supported live via a fleet tool call")
        else:
            lines.append(f"  scaling: not supported -- {scaling.get('reason', '')}")
    return "\n".join(lines)


def _rerun_check_proof(name: str) -> dict[str, Any] | None:
    """Re-run one check to prove an applied remediation, live where supported.

    ``None`` only when the name is not a registered check; a re-run that raises
    becomes an ``error`` result, never silent success.
    """
    rerun = CHECKS.get(name)
    if rerun is None:
        return None
    try:
        return rerun(live=True) if name in _LIVE_CHECK_NAMES else rerun()
    except Exception as exc:  # noqa: BLE001
        return _result(name, "error", f"re-run failed ({type(exc).__name__})")


def _interactive_outcome(
    check: dict[str, Any],
    confirm: Callable[[str], bool],
    executor: Callable[[dict[str, Any]], dict[str, Any]] | None,
    output: Callable[[str], None],
) -> dict[str, Any]:
    """Show one check's prescription, ask, and only then possibly apply it.

    Nothing is applied unless ``confirm`` returned ``True`` AND an ``executor``
    was supplied; every other path records a reason and applies nothing.
    """
    output(_format_prescription(check))
    approved = bool(confirm(f"Apply the remediation for {check['name']!r}? [y/N]: "))
    outcome: dict[str, Any] = {
        "name": check["name"],
        "confirmed": approved,
        "applied": False,
    }
    if not approved:
        outcome["reason"] = "not confirmed"
        return outcome
    if executor is None:
        outcome["reason"] = (
            "PLAN-ONLY: no executor configured -- hand this prescription to an "
            "operator or a reviewed `graph_orchestrate action=execute_agent` run"
        )
        return outcome
    try:
        exec_result = executor(check)
    except Exception as exc:  # noqa: BLE001 - interactive_apply is a defensive boundary
        outcome["reason"] = f"executor failed ({type(exc).__name__})"
        return outcome
    outcome["applied"] = bool(exec_result.get("applied"))
    outcome["executor_result"] = exec_result
    if outcome["applied"]:
        proof = _rerun_check_proof(check["name"])
        if proof is not None:
            outcome["proof"] = proof
    return outcome


def interactive_apply(
    report: dict[str, Any],
    *,
    confirm: Callable[[str], bool] | None = None,
    executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    output: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Walk the operator through applying each not-ok check's prescription.

    SAFETY-CRITICAL DIRECTION: nothing is ever applied without an explicit
    ``True`` from ``confirm``. The default ``confirm`` refuses everything --
    calling this with no arguments over a report full of failing checks is
    always a no-op. This is "default to dry-run" made structural, not just
    documented.

    ``executor`` is the only thing that can make this function mutate a live
    deployment. When it is ``None`` (the default) a confirmed step still never
    calls a fleet tool: it records that the plan is PLAN-ONLY and must be
    handed to an operator or a reviewed ``graph_orchestrate
    action=execute_agent`` run. GraphOS has no generic manifest-apply backend;
    deployment effects require an explicitly injected operator executor.
    When an ``executor`` genuinely applies a
    step (``exec_result["applied"]`` is truthy), the underlying check is
    re-run — live, when it supports ``live=`` — to prove the result rather
    than trusting the executor's say-so.
    """

    def _refuse(_prompt: str) -> bool:
        return False

    confirm = confirm or _refuse
    outcomes: list[dict[str, Any]] = []
    for check in report.get("checks", []):
        if check["status"] not in ("warn", "fail"):
            continue
        if not check.get("prescription"):
            continue
        outcomes.append(_interactive_outcome(check, confirm, executor, output))
    return outcomes


def _doctor_arg_parser() -> Any:
    """The ``agent-utilities-doctor`` console argument parser."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="agent-utilities-doctor",
        description="Holistic health sweep of an agent-utilities deployment.",
    )
    parser.add_argument("--only", nargs="*", choices=list(CHECKS), default=None)
    parser.add_argument(
        "--fix", action="store_true", help="Run safe auto-remediations."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Prove live graph connections, MCP, Langfuse, and native "
            "ProgramOptimize capabilities."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text."
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run the host DEPENDENCY preflight (runtimes/tools) instead of the deployment sweep.",
    )
    parser.add_argument(
        "--profile",
        default="tiny",
        help="Deployment profile for --preflight (tiny | single-node-prod | enterprise).",
    )
    parser.add_argument(
        "--component",
        dest="components",
        action="append",
        default=None,
        help="UI component to preflight (repeatable): agent-webui | geniusbot | agent-terminal-ui.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "After the sweep, walk through each not-ok check's prescription "
            "(manifest + config keys + gotcha) and ask before doing anything. "
            "Never applies without explicit confirmation; a non-interactive "
            "terminal always declines."
        ),
    )
    return parser


def _run_preflight_cli(args: Any) -> int:
    """``--preflight``: the host DEPENDENCY sweep instead of the deployment one."""
    import json

    from .preflight import run_preflight

    report = run_preflight(args.profile, args.components)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report, title="agent-utilities preflight")
    return 0 if report["status"] != "blocked" else 1


def _run_interactive_cli(report: dict[str, Any]) -> None:
    """``--interactive``: walk the prescriptions; a non-tty always declines."""
    import sys

    def _confirm(prompt: str) -> bool:
        if not sys.stdin.isatty():
            print(f"{prompt} (non-interactive terminal — declining)")
            return False
        return input(prompt).strip().lower() in ("y", "yes")

    outcomes = interactive_apply(report, confirm=_confirm)
    if not outcomes:
        return
    print("\ninteractive apply summary:")
    for outcome in outcomes:
        line = (
            f"  {outcome['name']}: confirmed={outcome['confirmed']} "
            f"applied={outcome['applied']}"
        )
        if outcome.get("reason"):
            line += f" — {outcome['reason']}"
        print(line)


def main(argv: list[str] | None = None) -> int:
    """``agent-utilities-doctor`` console entry."""
    import json

    args = _doctor_arg_parser().parse_args(argv)
    if args.preflight:
        return _run_preflight_cli(args)

    report = run_doctor(args.only, fix=args.fix, live=args.live)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    if args.interactive:
        _run_interactive_cli(report)
    return 0 if report["status"] != "unhealthy" else 1


def _print_human(report: dict[str, Any], title: str = "agent-utilities doctor") -> None:
    glyph = {"ok": "✓", "warn": "!", "fail": "✗", "error": "✗", "skip": "·"}
    print(f"{title} — {report['status'].upper()}\n")
    for r in report["checks"]:
        line = f"  {glyph.get(r['status'], '?')} {r['name']:<14} {r['detail']}"
        print(line)
        if r["status"] in ("warn", "fail", "error"):
            if r.get("remediation"):
                print(f"      → fix: {r['remediation']}")
            if r.get("skill"):
                print(f"      → skill: {r['skill']}")
    print(f"\n{report['summary']}")


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
