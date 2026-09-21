"""REST route mounting for the native graph-os action runtime."""

from __future__ import annotations

from functools import partial
from typing import Any

from pydantic import TypeAdapter

from graph_os.gateway.schemas.graph_ingest import (
    GraphToolResponse,
    GraphWriteEdgeDeleteRequest,
)
from graph_os.mcp_server import runtime


def _route(app: Any, prefix: str, path: str, handler: Any, methods: list[str]) -> None:
    app.add_route(prefix + path, handler, methods=methods)


def _route_typed(
    app: Any,
    prefix: str,
    path: str,
    handler: Any,
    methods: list[str],
    *,
    response_model: type,
    summary: str,
    description: str,
    request_model: type | Any,
    request_body_required: bool = True,
) -> None:
    """Mount a FastAPI-documented route, with a Starlette fallback."""

    if not hasattr(app, "add_api_route"):
        app.add_route(prefix + path, handler, methods=methods)
        return
    if hasattr(request_model, "model_json_schema"):
        body_schema = request_model.model_json_schema()
    else:
        body_schema = TypeAdapter(request_model).json_schema()
    app.add_api_route(
        prefix + path,
        handler,
        methods=methods,
        response_model=response_model,
        summary=summary,
        description=description,
        openapi_extra={
            "requestBody": {
                "required": request_body_required,
                "content": {"application/json": {"schema": body_schema}},
            }
        },
    )


def _mount_mining_routes(route: Any) -> None:
    if "graph_mine" not in runtime.ACTION_TOOL_ROUTES:
        return
    for action in runtime.MINING_ACTIONS:
        route(
            f"/mining/{action}",
            runtime._make_mining_endpoint(action),
            ["POST"],
        )


def mount_rest_routes(app, prefix: str = "") -> None:
    """Mount the full Knowledge Graph REST surface onto ``app``.

    ``app`` is any Starlette/FastAPI application exposing ``add_route``. Every
    path is prepended with ``prefix`` (the API gateway mounts these under
    ``/api``). Handlers dispatch through ``REGISTERED_TOOLS`` — call
    :func:`ensure_tools_registered` first.

    This is the single source of truth for the KG REST route table. The
    ``graph-os`` MCP server itself is now a thin FastMCP wrapper (MCP tools
    only); the REST API is served centrally by :mod:`graph_os.gateway` so the
    table never drifts between the two.
    """
    from agent_utilities.core.sessions import (
        cancel_goal,
        cancel_session_run,
        create_goal,
        delete_session,
        get_all_sessions,
        get_goal_iterations,
        get_session_details,
        list_goals,
        submit_session_reply,
    )

    from graph_os.gateway.schemas.graph_analyze import (
        SearchDiscoverRequest,
        SearchQueryTopKRequest,
        SearchTextResponse,
    )

    route = partial(_route, app, prefix)
    route_typed = partial(_route_typed, app, prefix)

    # ── Sessions & goals (durable Starlette handlers in core.sessions) ──
    route("/sessions", get_all_sessions, ["GET"])
    route("/sessions/{session_id}", get_session_details, ["GET"])
    route("/sessions/{session_id}", delete_session, ["DELETE"])
    route("/sessions/{session_id}/reply", submit_session_reply, ["POST"])
    route("/sessions/{session_id}/cancel", cancel_session_run, ["POST"])
    route("/goals", create_goal, ["POST"])
    route("/goals", list_goals, ["GET"])
    route("/goals/{goal_id}/iterations", get_goal_iterations, ["GET"])
    route("/goals/{goal_id}/cancel", cancel_goal, ["POST"])

    # ── Tools introspection / toggles ──
    route("/tools", runtime.get_tools_endpoint, ["GET"])
    route("/tools/toggle", runtime.toggle_tool_endpoint, ["POST"])

    # ── Bilateral graph execution (action-routed) ──
    route("/graph/query", runtime.graph_query_endpoint, ["POST"])
    route("/query/tabular", runtime.tabular_query_endpoint, ["POST"])
    route("/graph/search", runtime.graph_search_endpoint, ["POST"])
    # Collapsed, typed graph_write dispatch (CONSOLIDATION: see the
    # `GraphWriteAction` discriminated union above runtime.graph_write_endpoint's
    # definition) — the first FastAPI-documented route in this file.
    route_typed(
        "/graph/write",
        runtime.graph_write_endpoint,
        ["POST"],
        response_model=GraphToolResponse,
        summary="Write a node/edge or run another graph_write action",
        description=(
            "Collapsed, action-routed graph_write endpoint. Validates the "
            "body against a discriminated union on 'action' covering "
            "add_node, add_edge, delete_edge, bulk_ingest, log_chat, "
            "register_execution (formerly separate granular routes under "
            "/graph/write/{node,edge,bulk,chat,execution}, now removed), "
            "plus every other graph_write action (delete_node, "
            "register_external_graph, compare_and_set, store_memory, "
            "recall_memory, recall_media, submit_sdd, check_loop). See the "
            "GraphWriteAction union's member models for the exact per-action "
            "request shape."
        ),
        request_model=runtime.GraphWriteAction,
    )
    route_typed(
        "/graph/write",
        runtime.graph_write_delete_edge_endpoint,
        ["DELETE"],
        response_model=GraphToolResponse,
        summary="Delete an edge (graph_write action=delete_edge)",
        description=(
            "Deletes one edge identified by source_id/target_id/rel_type. "
            "Equivalent to POST /graph/write with action='delete_edge'; "
            "kept as a dedicated DELETE verb on the same collapsed path so "
            "a REST-verb-first caller does not lose the capability the "
            "removed DELETE /graph/write/edge granular route had."
        ),
        request_model=GraphWriteEdgeDeleteRequest,
    )
    route("/graph/ingest", runtime.graph_ingest_endpoint, ["POST"])
    route("/graph/analyze", runtime.graph_analyze_endpoint, ["POST"])
    route("/graph/code", runtime.graph_code_endpoint, ["POST"])
    route("/graph/research", runtime.graph_research_endpoint, ["POST"])
    route("/graph/evaluate", runtime.graph_evaluate_endpoint, ["POST"])
    route("/graph/explain", runtime.graph_explain_endpoint, ["POST"])
    route("/graph/observe", runtime.graph_observe_endpoint, ["POST"])
    route("/graph/orchestrate", runtime.graph_orchestrate_endpoint, ["POST"])
    route("/graph/configure", runtime.graph_configure_endpoint, ["POST"])

    # ── Granular query ──
    route("/graph/query/federated", runtime.graph_query_federated_endpoint, ["POST"])

    # ── Granular search ──
    # These mode-fixed adapters share one implementation, while each remains a
    # separately documented route with the same request/response schemas that
    # describe its existing wire behavior.
    for _path, _handler, _summary, _description, _request_model in (
        (
            "/graph/search/concept",
            runtime.graph_search_concept_endpoint,
            "Search concepts",
            "Search the knowledge graph using concept retrieval.",
            SearchQueryTopKRequest,
        ),
        (
            "/graph/search/analogy",
            runtime.graph_search_analogy_endpoint,
            "Search by analogy",
            "Search the knowledge graph for analogous concepts.",
            SearchQueryTopKRequest,
        ),
        (
            "/graph/search/memory",
            runtime.graph_search_memory_endpoint,
            "Search memories",
            "Search retained graph memories.",
            SearchQueryTopKRequest,
        ),
        (
            "/graph/search/discover",
            runtime.graph_search_discover_endpoint,
            "Discover graph capabilities",
            "Discover ingested graph capabilities matching a query.",
            SearchDiscoverRequest,
        ),
        (
            "/graph/search/dci",
            runtime.graph_search_dci_endpoint,
            "Search with DCI",
            "Search the knowledge graph using DCI retrieval.",
            SearchQueryTopKRequest,
        ),
    ):
        route_typed(
            _path,
            _handler,
            ["POST"],
            response_model=SearchTextResponse,
            summary=_summary,
            description=_description,
            request_model=_request_model,
            request_body_required=False,
        )

    # ── Granular write (out of this consolidation's scope — see kg_server.py's
    # collapsed-write comment block above runtime.graph_write_endpoint) ──
    route(
        "/graph/write/node/{node_id}",
        runtime.graph_write_delete_node_endpoint,
        ["DELETE"],
    )
    route("/graph/write/external", runtime.graph_write_external_endpoint, ["POST"])
    route("/graph/write/memory", runtime.graph_write_memory_endpoint, ["POST"])
    route(
        "/graph/write/memory/recall",
        runtime.graph_write_memory_recall_endpoint,
        ["POST"],
    )
    # CONCEPT:AU-KG.ontology.federation-runtime — federation: explicit twin for ontology package-sync.
    route(
        "/graph/ontology/sync-packages",
        runtime.graph_ontology_sync_packages_endpoint,
        ["POST"],
    )
    # CONCEPT:AU-KG.ontology.stardog-catalog-overwrite / stardog-catalog-import — Stardog catalog twins.
    route(
        "/graph/ontology/publish-stardog",
        runtime.graph_ontology_publish_stardog_endpoint,
        ["POST"],
    )
    route(
        "/graph/ontology/import-stardog",
        runtime.graph_ontology_import_stardog_endpoint,
        ["POST"],
    )
    route("/graph/write/sdd", runtime.graph_write_sdd_endpoint, ["POST"])

    # ── Granular ingest ──
    route("/graph/ingest/submit", runtime.graph_ingest_submit_endpoint, ["POST"])
    route("/graph/ingest/corpus", runtime.graph_ingest_corpus_endpoint, ["POST"])
    route("/graph/ingest/jobs", runtime.graph_ingest_jobs_endpoint, ["GET"])
    route("/connector/sources", runtime.connector_sources_endpoint, ["GET"])
    route("/connector/run", runtime.connector_run_endpoint, ["POST"])
    route(
        "/graph/ingest/job/{job_id}", runtime.graph_ingest_job_status_endpoint, ["GET"]
    )
    route(
        "/graph/ingest/rebuild-indexes",
        runtime.graph_ingest_rebuild_indexes_endpoint,
        ["POST"],
    )
    route("/graph/ingest/observe", runtime.graph_ingest_observe_endpoint, ["POST"])
    route(
        "/graph/ingest/materialize", runtime.graph_ingest_materialize_endpoint, ["POST"]
    )
    route(
        "/graph/ingest/materialize-source",
        runtime.graph_ingest_materialize_source_endpoint,
        ["POST"],
    )
    route("/graph/ingest/sync", runtime.graph_ingest_sync_endpoint, ["POST"])
    route("/graph/ingest/reflect", runtime.graph_ingest_reflect_endpoint, ["POST"])
    route(
        "/graph/ingest/agent-toolkit",
        runtime.graph_ingest_agent_toolkit_endpoint,
        ["POST"],
    )
    route(
        "/graph/ingest/knowledge-pack",
        runtime.graph_ingest_knowledge_pack_endpoint,
        ["POST"],
    )

    # ── Granular analyze ──
    route(
        "/graph/analyze/synthesize", runtime.graph_analyze_synthesize_endpoint, ["POST"]
    )
    route(
        "/graph/analyze/process-writeback",
        runtime.graph_analyze_process_writeback_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/deep-extract",
        runtime.graph_analyze_deep_extract_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/background-research",
        runtime.graph_analyze_background_research_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/relevance-sweep",
        runtime.graph_analyze_relevance_sweep_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/blast-radius",
        runtime.graph_analyze_blast_radius_endpoint,
        ["GET"],
    )
    route("/graph/analyze/inspect", runtime.graph_analyze_inspect_endpoint, ["GET"])
    route(
        "/graph/analyze/call-graph", runtime.graph_analyze_call_graph_endpoint, ["GET"]
    )
    route(
        "/graph/analyze/similar-code",
        runtime.graph_analyze_similar_code_endpoint,
        ["GET"],
    )
    route("/graph/analyze/routes", runtime.graph_analyze_routes_endpoint, ["GET"])
    route(
        "/graph/analyze/change-coupling",
        runtime.graph_analyze_change_coupling_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/code-evolution",
        runtime.graph_analyze_code_evolution_endpoint,
        ["POST"],
    )
    route("/graph/analyze/adr", runtime.graph_analyze_adr_endpoint, ["POST"])
    route(
        "/graph/analyze/harness-gate",
        runtime.graph_analyze_harness_gate_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/code-context",
        runtime.graph_analyze_code_context_endpoint,
        ["POST"],
    )
    route(
        "/graph/analyze/code-metrics",
        runtime.graph_analyze_code_metrics_endpoint,
        ["GET"],
    )
    route(
        "/graph/analyze/arch-report",
        runtime.graph_analyze_arch_report_endpoint,
        ["GET"],
    )
    route("/graph/analyze/explain", runtime.graph_analyze_explain_endpoint, ["POST"])
    route(
        "/graph/analyze/cross-repo-usages",
        runtime.graph_analyze_cross_repo_usages_endpoint,
        ["GET"],
    )
    route("/graph/analyze/context", runtime.graph_analyze_context_endpoint, ["POST"])
    route(
        "/graph/analyze/evaluate-alpha",
        runtime.graph_analyze_evaluate_alpha_endpoint,
        ["POST"],
    )
    route("/graph/analyze/evaluate", runtime.graph_analyze_evaluate_endpoint, ["POST"])
    route(
        "/graph/analyze/evolve-model",
        runtime.graph_analyze_evolve_model_endpoint,
        ["POST"],
    )
    route("/graph/analyze/forecast", runtime.graph_analyze_forecast_endpoint, ["POST"])
    route("/graph/analyze/causal", runtime.graph_analyze_causal_endpoint, ["POST"])
    route(
        "/graph/analyze/invariant", runtime.graph_analyze_invariant_endpoint, ["POST"]
    )
    route(
        "/graph/analyze/security-scan",
        runtime.graph_analyze_security_scan_endpoint,
        ["POST"],
    )

    # ── Granular configure ──
    route("/graph/configure/secret", runtime.graph_configure_secret_endpoint, ["POST"])
    route(
        "/graph/configure/vault-sync",
        runtime.graph_configure_vault_sync_endpoint,
        ["POST"],
    )
    route(
        "/graph/configure/register-mcp",
        runtime.graph_configure_register_mcp_endpoint,
        ["POST"],
    )
    route(
        "/graph/configure/install-hooks",
        runtime.graph_configure_install_hooks_endpoint,
        ["POST"],
    )
    route(
        "/graph/configure/uninstall-hooks",
        runtime.graph_configure_uninstall_hooks_endpoint,
        ["POST"],
    )
    route("/graph/configure/doctor", runtime.graph_configure_doctor_endpoint, ["POST"])

    # ── Collapsed action-routed twins (full MCP⇄REST parity) ──
    # The core graph_* tools above already have bespoke endpoints; every
    # other MCP tool in runtime.ACTION_TOOL_ROUTES (context, feedback, hydrate, sessions,
    # goals, document_process, source_connector, ontology_*, object_*) is served
    # by the generic factory so the REST surface reaches everything MCP can.
    _bespoke_action_tools = {
        "graph_query",
        "tabular_query",
        "graph_search",
        "graph_write",
        "graph_ingest",
        "graph_analyze",
        "graph_orchestrate",
        "graph_configure",
        # graph_mine has a bespoke endpoint (natural mining body → the same
        # _execute_tool core) mounted below (CONCEPT:EG-KG.mining.frequent-itemset-mining).
        "graph_mine",
        # graph_learn likewise has bespoke natural-body twins (CONCEPT:EG-KG.graphlearn.link-predictor).
        "graph_learn",
        # graph_mine_deep likewise has bespoke natural-body twins (CONCEPT:AU-KG.mining.dsm-forecast-delegation).
        "graph_mine_deep",
    }
    for _tool, _path in runtime.ACTION_TOOL_ROUTES.items():
        if _tool in _bespoke_action_tools:
            continue
        route(_path, runtime._make_tool_endpoint(_tool), ["POST"])

    # Data-mining REST twins (CONCEPT:EG-KG.mining.frequent-itemset-mining) — one natural-body
    # /api/mining/<action> endpoint per graph_mine action (the full 18-action surface —
    # see runtime.MINING_ACTIONS), each dispatching the SAME graph_mine _execute_tool core
    # (surface parity).
    _mount_mining_routes(route)

    # Graph-learning REST twins (CONCEPT:EG-KG.graphlearn.link-predictor) — one
    # natural-body /api/graphlearn/<action> endpoint per graph_learn action (fit|predict),
    # each dispatching the SAME graph_learn _execute_tool core (surface parity).
    if "graph_learn" in runtime.ACTION_TOOL_ROUTES:
        for _gl_action in runtime.GRAPHLEARN_ACTIONS:
            route(
                f"/graphlearn/{_gl_action}",
                runtime._make_graphlearn_endpoint(_gl_action),
                ["POST"],
            )

    # Deep-mining delegation REST twins (CONCEPT:AU-KG.mining.dsm-forecast-delegation — Phase 6) — one
    # natural-body /api/mining/deep/<action> endpoint per graph_mine_deep action
    # (deep_forecast|deep_classify|autoencoder_anomaly|xgboost|embed), each
    # dispatching the SAME graph_mine_deep _execute_tool core (surface parity).
    if "graph_mine_deep" in runtime.ACTION_TOOL_ROUTES:
        for _deep_action in runtime.DEEP_MINING_ACTIONS:
            route(
                f"/mining/deep/{_deep_action}",
                runtime._make_mining_deep_endpoint(_deep_action),
                ["POST"],
            )
