"""Serving lifecycle for the native graph-os MCP application."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from agent_connector_sdk.mcp.content import register_connector_content
from agent_utilities.core.config import setting

from graph_os.mcp_server import runtime
from graph_os.mcp_server.composition import (
    install_gateway_application,
    start_composed_services,
)
from graph_os.semantic_content import default_content_providers

logger = logging.getLogger(__name__)

_FLEET_EMBED_MODEL: Any = None


def _register_semantic_content(mcp: Any) -> None:
    """Expose independently owned semantic artifacts through the SDK contract.

    ConnectorPack capture consumes these MCP resources and EG ``AttachPack``
    remains the sole authority that interprets/activates their SHACL content.
    GraphOS deliberately performs no local shape parsing or attachment.
    """

    for provider in default_content_providers():
        register_connector_content(mcp, provider())


def _fleet_embed_fn():
    """Return a sync batch-embed callable ``(texts) -> list[vector]`` for find_tools'
    semantic tool ranking, backed by graph-os's own embedding model (built lazily +
    cached on first use). The model is remote (vLLM) and sync, so the fleet loader calls
    this OFF-THREAD. Any construction/inference failure is swallowed by the caller, which
    then degrades to token-overlap ranking — so this never blocks fleet loading."""

    def _embed(texts):
        global _FLEET_EMBED_MODEL
        if _FLEET_EMBED_MODEL is None:
            from agent_utilities.core.embedding_utilities import create_embedding_model

            _FLEET_EMBED_MODEL = create_embedding_model()
        model = _FLEET_EMBED_MODEL
        batch = getattr(model, "get_text_embedding_batch", None)
        if callable(batch):
            return batch(list(texts))
        return [model.get_text_embedding(t) for t in texts]

    return _embed


def _configure_graphos_otel() -> None:
    """Activate the canonical metadata-only OTLP pipeline when configured."""

    if not setting("ENABLE_OTEL", False):
        return
    try:
        from agent_utilities.observability.custom_observability import setup_otel

        setup_otel(service_name="graph-os")
    except Exception as exc:  # noqa: BLE001 - observability cannot prevent serving
        logger.warning(
            "GraphOS OTLP setup failed; trace export is disabled: %s",
            exc,
        )


def _configure_telemetry_engine_otel() -> None:
    """Eagerly start the standard-env-var OTLP trace pipeline (X2).

    CONCEPT:AU-OS.observability.telemetry-observability — independent of
    ``_configure_graphos_otel``'s ``ENABLE_OTEL``-gated Logfire/Langfuse
    pipeline: :class:`~agent_utilities.observability.TelemetryEngine` self-gates
    purely on the standard ``OTEL_EXPORTER_OTLP_ENDPOINT``/``OTEL_SERVICE_NAME``/
    ``OTEL_TRACES_EXPORTER`` vars (falling back to ``EPISTEMIC_GRAPH_OBS_ADDR``),
    so no new env var is introduced here. This is the ONE process-bootstrap
    call site that activates it for the graph-os MCP server — never per-request.
    """
    try:
        from agent_utilities.observability import get_telemetry_engine

        configured = get_telemetry_engine().is_otel_configured()
        logger.info(
            "GraphOS OTLP trace export (standard env vars): %s",
            "enabled" if configured else "disabled",
        )
    except Exception as exc:  # noqa: BLE001 - observability cannot prevent serving
        logger.warning(
            "GraphOS TelemetryEngine OTel setup failed: %s",
            exc,
        )


def _preflight_mcp_sdk_floor() -> None:
    """Fail startup loudly when the installed MCP SDK is below the declared floor.

    CONCEPT:AU-ECO.mcp.protocol-compat-bridge — closes D-OB-18.

    The category defect this exists for is that source-vs-installed divergence was
    INVISIBLE: graph-os source targeting fastmcp 4 / mcp 2 ran for weeks on an image
    that shipped fastmcp 3.4.5 / mcp 1.29.0, and the only symptom was a single ERROR
    log line as `attach_fleet_loader` lost every fleet meta-tool to
    ``ImportError: cannot import name 'MCPError'``. A rebuilt image with no assertion
    just resets that clock, so the assertion runs here, at startup, as well as at
    image-build time.

    Enforcement is a hook, not a hardcoded policy: ``MCP_SDK_FLOOR_ENFORCE`` selects
    ``error`` (default — refuse to start, because a graph-os that silently loses its
    fleet surface is worse than one that will not come up) or ``warn`` (log and
    continue, for an operator who is knowingly running a mismatched pair during a
    migration).
    """
    from agent_utilities.mcp.protocol_compat import check_mcp_sdk_floor

    result = check_mcp_sdk_floor()
    if result["ok"] is True:
        logger.info("MCP SDK floor OK: %s", result["detail"])
        return
    if result["ok"] is None:
        logger.warning("MCP SDK floor check skipped: %s", result["detail"])
        return

    mode = str(setting("MCP_SDK_FLOOR_ENFORCE", "error") or "error").strip().lower()
    message = (
        f"installed MCP SDK does not satisfy the declared [mcp] floor: {result['detail']}. "
        "The runtime image and this source tree have diverged — rebuild the image "
        "(docker/graphos-unified.Dockerfile) so its dependency closure matches the "
        "source it serves. Set MCP_SDK_FLOOR_ENFORCE=warn to start anyway."
    )
    if mode == "warn":
        logger.error("graph-os starting with a mismatched MCP SDK: %s", message)
        return
    raise RuntimeError(message)


def mcp_server() -> None:
    """``graph-os`` MCP server entry point (registered as console_scripts).

    Thin FastMCP wrapper following the standard ``mcp_server.py`` template: it
    serves ONLY the MCP tool surface, over ``stdio`` or ``streamable-http``,
    selected by the standard ``--transport/--host/--port`` args
    from :func:`create_mcp_server`. The REST API (``/graph/*``, ``/sessions``,
    ``/goals``, ``/tools``) is centralized in the API gateway
    (:mod:`graph_os.gateway`) — see :func:`_mount_rest_routes`.
    """
    from agent_utilities.core.config import load_config

    load_config()  # resolve settings through the one shared XDG config.json
    install_gateway_application()
    _preflight_mcp_sdk_floor()
    _configure_graphos_otel()
    _configure_telemetry_engine_otel()
    os.environ["IS_KG_SERVER"] = "true"
    args, mcp, middlewares = runtime._build_server()
    _register_semantic_content(mcp)
    from graph_os.fleet.catalog_reader import DeferredFleetCatalogReader

    fleet_catalog_reader = DeferredFleetCatalogReader()

    # Apply the middleware stack assembled by the factory.
    for middleware in middlewares:
        mcp.add_middleware(middleware)

    # Fold in the MCP fleet-loader (retires the standalone mcp-multiplexer): graph-os's
    # own tools stay always-on; this adds find_tools/load_tools/... so the SAME server
    # reaches the rest of the MCP fleet on demand. Attached AFTER the factory middlewares
    # so per-session tool visibility runs with identity/auth already applied. Only for a
    # directly-served process — the embedded API-gateway build owns no serving loop.
    # The eight meta-tools this attaches (find_tools/list_catalog/load_tools/
    # unload_tools/catalog_refresh/catalog_dispatch/catalog_session_resume/
    # multiplexer_status) plus the
    # session-visibility middleware are
    # MODE-INDEPENDENT infrastructure — they are the only way to reach anything
    # the active MCP_TOOL_MODE holds back, so they must be present under intent,
    # condensed, verbose AND both. A failure here is therefore NOT survivable:
    # the previous `except Exception: logger.error(...)` downgraded it to a log
    # line and served a silently wrong surface (an SDK-rename ImportError in
    # child_resilience left graph-os exposing 118 ungated tools with no
    # load_tools at all). Fail loud, preserving __cause__.
    # CONCEPT:AU-ECO.mcp.fleet-meta-tools-always-on
    try:
        from graph_os.fleet.multiplexer import attach_fleet_loader

        # Inject graph-os's own embedding model so find_tools ranks fleet tools by
        # query↔description MEANING (semantic), not just literal token overlap.
        fleet_mux = attach_fleet_loader(
            mcp,
            catalog_reader=fleet_catalog_reader,
            embed_fn=_fleet_embed_fn(),
            authority_scope=runtime.verified_tool_session_scope,
        )
    except Exception as exc:
        raise RuntimeError(
            "graph-os fleet loader attach failed: the fleet meta-tools "
            "(find_tools/list_catalog/load_tools/unload_tools/catalog_refresh/"
            "catalog_dispatch/catalog_session_resume/multiplexer_status) "
            "and the session-visibility middleware could not be registered, so the "
            "served tool surface would be wrong under every MCP_TOOL_MODE."
        ) from exc

    transport = getattr(args, "transport", "stdio")
    host = getattr(args, "host", "127.0.0.1")
    port = int(getattr(args, "port", 8000))

    bootstrap_session = runtime._mint_process_session(transport)
    runtime._PROCESS_SESSION = bootstrap_session if transport == "stdio" else None
    runtime.set_process_session(runtime._PROCESS_SESSION)
    runtime._start_process_authority_supervisor(bootstrap_session)
    # Readiness probes the live fleet/goal authority, which is a real graph read
    # and therefore needs a bound session. `runtime._PROCESS_SESSION` is deliberately
    # None on network transports (it is a stdio fallback, and must not become a
    # way for a request path to pick up identity it never authenticated), so
    # readiness gets its own narrowly-scoped handle on the process authority.
    # Without this the probe measured its own missing identity instead of the
    # authority, reported the goal store `unavailable`, and held /health/ready
    # at 503 forever on every served deployment.
    readiness_authority_owner = runtime._set_readiness_authority(bootstrap_session)

    co_service_supervisor = None
    try:
        logger.info("Starting graph-os MCP server (transport=%s)", transport)

        from agent_utilities.mcp.server_factory import mcp_network_run_kwargs
        from agent_utilities.security.request_identity import (
            apply_served_security_profile,
        )

        # Network transports serve many clients at once: enforce server-validated
        # identity + tenant scoping, or fail loud (CONCEPT:AU-OS.identity.authenticated-identity-enforcement). No-op for stdio.
        apply_served_security_profile(
            transport,
            transport_auth_configured=(
                str(getattr(args, "auth_type", "none") or "none").lower() != "none"
            ),
        )

        # Stdout purity on the stdio transport needs no call here: it is owned
        # fd-level by the MCP SDK's own ``stdio_server()`` for the scope of the
        # later stdio-serve call below (see the "Stdio JSON-RPC purity" note in
        # server_factory.py) — that covers every co-service thread started
        # below too, since they share this process's file-descriptor table for
        # as long as serving blocks. The residual window before that call
        # claims fd 1 (engine bootstrap, co-service startup, this function
        # itself) is covered by the static "no print() in the served package"
        # gate (``scripts/check_no_stdout_writes.py``), not a runtime patch.
        # No-op for network transports either way (they don't own stdout as a
        # protocol channel).

        # Bind the minted process session (+ its verified actor) as ambient
        # authority before engine bootstrap. An explicit client role remains a
        # hard serving-plane boundary; this entrypoint never promotes itself to
        # the host that owns maintenance, workers, or autonomous loops.
        from agent_utilities.core.config import config
        from agent_utilities.knowledge_graph.core.session import use_session
        from agent_utilities.security.brain_context import use_actor

        with use_actor(bootstrap_session.actor), use_session(bootstrap_session):
            runtime._start_engine_bootstrap(bootstrap_session)

            from graph_os.mcp_server.catalog_composition import (
                compose_catalog_authorities,
            )

            asyncio.run(
                compose_catalog_authorities(
                    engine=runtime._get_engine(),
                    session=bootstrap_session,
                    deferred_fleet=fleet_catalog_reader,
                    multiplexer=fleet_mux,
                )
            )

            # Self-composing co-services, phase 2: messaging now that a real engine
            # exists. Credentials keep outbound sending available, but the explicit
            # MESSAGING_INTAKE_ENABLED deployment intent (false by default) is the
            # only way this request container may enter the shared native lease
            # boundary. When ENABLE_WEB_UI is true, the packaged agent-webui is
            # started in-process by this same supervisor as a separately bound,
            # independently restartable co-service.
            co_service_supervisor = start_composed_services(
                bootstrap_session,
                runtime._get_engine(),
                messaging_intake_enabled=config.messaging_intake_enabled,
            )

        if transport == "stdio":
            mcp.run(transport="stdio")
        elif transport == "streamable-http":
            mcp.run(
                transport="streamable-http",
                host=host,
                port=port,
                **mcp_network_run_kwargs(args),
            )
        else:
            raise ValueError("graph-os transport must be 'stdio' or 'streamable-http'")
    finally:
        if co_service_supervisor is not None:
            co_service_supervisor.stop_all()
        runtime._PROCESS_SESSION = None
        runtime.set_process_session(None)
        runtime._stop_process_authority_supervisor()
        runtime._release_readiness_authority(readiness_authority_owner)
        # Best-effort teardown of any lazily-mounted fleet children.
        if fleet_mux is not None:
            try:
                asyncio.run(fleet_mux.aclose())
            except Exception as exc:  # noqa: BLE001 — best-effort teardown of a lazily-mounted fleet child at process exit
                logger.warning("fleet loader close failed: %s", exc)
