"""Centralized Knowledge Graph REST surface for the API gateway.

CONCEPT:AU-ECO.mcp.knowledge-graph-exposure — Knowledge Graph API Gateway

The full Knowledge Graph REST API (``/graph/*``, ``/sessions``, ``/goals``,
``/tools``) is now owned by this gateway rather than the ``graph-os`` MCP server,
which has been slimmed to a thin FastMCP wrapper (MCP tools only). Funnelling all
graph HTTP traffic through this single persistent process eliminates the
embedded-DB file-lock contention that arises when many clients
(agent-terminal-ui, geniusbot, subagents, ingestion scripts) each open the graph
store directly.

The canonical action route table is supplied through
:class:`graph_os.gateway.ports.GatewayApplicationPort`, so this transport does
not import the application implementation. :func:`register_graph_routes` is
the single gateway composition entry point.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

#: The shared cross-tenant graph every verified reader may also query.
COMMONS_GRAPH = "__commons__"


def _readable_graphs(session: Any) -> tuple[str, ...]:
    """The caller's own tenant graph first, then the shared commons graph."""
    tenant = str(session.tenant or "").strip()
    return tuple(dict.fromkeys(graph for graph in (tenant, COMMONS_GRAPH) if graph))


def _graph_client(graph: str) -> Any:
    """The process's session-routed epistemic-graph client for ``graph``."""
    from graph_os.gateway.ports import gateway_application

    return gateway_application().graph_client(graph)


def _mount_sparql_route(app, prefix: str = "/api") -> None:
    """Mount ``{prefix}/sparql`` over the epistemic-graph native SPARQL method."""

    async def sparql_endpoint(request: Request) -> JSONResponse:
        from agent_utilities.api.session import resolve_session

        session = resolve_session(required_scope="kg:read")
        query = await _sparql_query(request)
        if not query:
            return JSONResponse(
                {"status": "error", "message": "missing 'query'"}, status_code=400
            )
        try:
            bindings = await _query_readable_graphs(query, session)
        except Exception as exc:
            logger.warning("SPARQL query failed: %s", exc)
            return JSONResponse(
                {"status": "error", "message": "SPARQL query failed"},
                status_code=500,
            )
        varnames = list(bindings[0].keys()) if bindings else []
        return JSONResponse(
            {
                "status": "success",
                "head": {"vars": varnames},
                "results": {"bindings": bindings},
            }
        )

    path = f"{prefix}/sparql"
    app.add_route(path, sparql_endpoint, methods=["GET", "POST"])
    logger.info("Mounted epistemic-graph SPARQL endpoint")


async def _sparql_query(request: Request) -> str | None:
    query = request.query_params.get("query")
    if query or request.method != "POST":
        return query
    try:
        body = await request.json()
        return body.get("query") if isinstance(body, dict) else None
    except Exception as exc:
        logger.warning("SPARQL JSON body unavailable; reading raw body: %s", exc)
        return (await request.body()).decode("utf-8", "replace") or None


async def _query_readable_graphs(query: str, session: Any) -> list[dict[str, Any]]:
    """Union SPARQL rows over the caller's readable graphs, tenant first.

    Each graph is answered by the engine under the caller's own verified
    claims; a graph the engine refuses is an error, never a silent omission.
    """
    claims = session.engine_verified_context()
    bindings: list[dict[str, Any]] = []
    for graph_name in _readable_graphs(session):
        client = _graph_client(graph_name)
        with client.use_verified_context(claims):
            bindings.extend(await client.rdf.sparql(query))
    return bindings


SQL_SCHEMA_PATH = "/graph/sql-schema"

SQL_SCHEMA_SUMMARY = "Introspect the engine SQL catalog (read-only, no caller SQL)"

SQL_SCHEMA_DESCRIPTION = (
    "Return the `catalogs -> tables/views -> columns` projection of the "
    "engine's synthesized, read-only `information_schema` -- the schema tree a "
    "catalog browser renders. Accepts an OPTIONAL JSON body "
    '`{"schema": "<name>"}`; it carries **no SQL**. Every statement issued is a '
    "server-authored constant "
    "(`graph_os.gateway.sql_catalog.CATALOG_STATEMENTS`) and the schema "
    "filter is validated as a SQL identifier and applied in Python, so there is "
    "no interpolation and no path to row data. Runs through the generated "
    "epistemic-graph SQL client for the caller's tenant graph, under the "
    "caller's own GraphSession (`kg:read`) and the engine's row-level "
    "isolation. Fails closed: an unreadable catalog is a typed 4xx/5xx error, "
    "never an empty success. The response's `capabilities` block reports which "
    "facets the engine can actually answer (primary keys and nullability are "
    "currently not tracked engine-side)."
)


async def _sql_schema_endpoint(request: Request) -> JSONResponse:
    """Serve ``POST /graph/sql-schema`` — the read-only catalog projection."""
    from agent_utilities.api.session import resolve_session

    from graph_os.gateway.sql_catalog import SqlSchemaUnavailable, sql_schema

    try:
        session = resolve_session(required_scope="kg:read")
    except Exception as exc:  # noqa: BLE001 — authz failures are 403, never 500
        logger.info("sql-schema denied: %s", exc)
        return JSONResponse(
            {
                "status": "error",
                "code": "forbidden",
                "message": "SQL catalog introspection denied",
            },
            status_code=403,
        )

    try:
        schema = await _sql_schema_filter(request)
        graph = _readable_graphs(session)[0]
        client = _graph_client(graph)
        with client.use_verified_context(session.engine_verified_context()):
            projection = await sql_schema(client, schema=schema)
        return JSONResponse(projection)
    except SqlSchemaUnavailable as exc:
        return JSONResponse(exc.as_payload(), status_code=exc.status_code)


async def _sql_schema_filter(request: Request) -> str | None:
    """The optional ``schema`` name from the request body (never SQL text)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an absent/blank body means "every schema"
        return None
    if not isinstance(body, dict):
        return None
    return body.get("schema")


def _mount_sql_schema_route(app, prefix: str = "/api") -> None:
    """Mount ``{prefix}/graph/sql-schema``.

    Registered with FastAPI's ``add_api_route`` (not raw ``add_route``) so the
    route is visible in the generated OpenAPI spec -- ``scripts/
    check_openapi_coverage.py`` ratchets on undocumented raw-Starlette routes,
    and a new one would be a regression.
    """
    path = f"{prefix}{SQL_SCHEMA_PATH}"
    if any(getattr(r, "path", None) == path for r in getattr(app, "routes", [])):
        return
    if hasattr(app, "add_api_route"):  # FastAPI
        app.add_api_route(
            path,
            _sql_schema_endpoint,
            methods=["POST"],
            name="graph_sql_schema",
            summary=SQL_SCHEMA_SUMMARY,
            description=SQL_SCHEMA_DESCRIPTION,
        )
    else:  # plain Starlette
        app.add_route(path, _sql_schema_endpoint, methods=["POST"])
    logger.info("Mounted read-only SQL catalog introspection at %s", path)


def register_graph_routes(app, prefix: str = "/api") -> None:
    """Mount the centralized Knowledge Graph REST surface onto ``app``.

    Args:
        app: The FastAPI/Starlette application to mount routes on.
        prefix: Path prefix for every route (default ``/api`` → e.g.
            ``/api/graph/query``, ``/api/sessions``).

    Registers all KG tools so handlers dispatch through ``REGISTERED_TOOLS``.
    Graph queries use the typed ``/api/graph/query`` action surface; there is no
    second raw query route.
    """
    from graph_os.gateway.ports import gateway_application

    # Populate REGISTERED_TOOLS without starting the MCP server's own engine
    # bootstrap — the gateway owns the engine/daemon lifecycle.
    application = gateway_application()
    application.ensure_tools_registered()

    # Per-tenant token-bucket rate limiting
    # (CONCEPT:AU-OS.observability.no-op-without-metrics). Added BEFORE
    # the identity middleware so it sits INSIDE it (Starlette: last added =
    # outermost) — the server-minted ActorContext is already in scope when
    # the bucket key (tenant → actor → client IP) is resolved. Disabled by
    # default (GATEWAY_RATE_LIMIT=0); /metrics and health paths are exempt.
    from agent_utilities.core.config import config

    if config.gateway_rate_limit > 0:
        from graph_os.gateway.rate_limit import GatewayRateLimitMiddleware

        app.add_middleware(GatewayRateLimitMiddleware)

    # Server-minted JWT identity
    # (CONCEPT:AU-OS.identity.authenticated-identity-enforcement).
    # Validates ``Authorization: Bearer`` via
    # the existing JWKS machinery, scopes the request to an authenticated
    # ActorContext + GraphSession, and rejects unauthenticated requests with 401.
    from agent_utilities.security.request_identity import ActorIdentityMiddleware

    app.add_middleware(ActorIdentityMiddleware)

    # Python-tier Prometheus metrics
    # (CONCEPT:AU-OS.observability.no-op-without-metrics). Added LAST so the
    # metrics middleware is OUTERMOST — auth rejections (401) and rate-limit
    # rejections (429) are counted too. GET /metrics remains behind the
    # identity middleware when KG authentication is required. Both the gateway and
    # the agent-webui backend mount through here, so both get the same
    # instrumentation. With prometheus_client absent (optional ``metrics``
    # extra) everything degrades to a no-op.
    if config.gateway_metrics:
        from agent_utilities.observability.gateway_metrics import (
            GatewayMetricsMiddleware,
            metrics_asgi_endpoint,
            metrics_endpoint,
        )

        app.add_middleware(GatewayMetricsMiddleware)
        if not any(getattr(r, "path", None) == "/metrics" for r in app.routes):
            if hasattr(app, "add_api_route"):  # FastAPI
                app.add_api_route(
                    "/metrics",
                    metrics_endpoint,
                    methods=["GET"],
                    include_in_schema=False,
                )
            else:  # plain Starlette
                app.add_route("/metrics", metrics_asgi_endpoint, methods=["GET"])

    application.mount_rest_routes(app, prefix=prefix)

    # SPARQL is answered by the epistemic-graph native SPARQL method over the
    # caller's readable graphs; GraphOS performs no RDF materialization.
    _mount_sparql_route(app, prefix=prefix)

    # Read-only SQL catalog introspection (CONCEPT:AU-KG.query.raw-python) — the
    # catalogs → tables/views → columns projection the agent-webui catalog
    # browser renders. Deliberately NOT a raw SQL passthrough: it takes no query
    # text, only an optional identifier-validated schema filter. See
    # graph_os/gateway/sql_catalog.py.
    _mount_sql_schema_route(app, prefix=prefix)

    # Native swarm supervisory plane (CONCEPT:AU-OS.safety.ontological-guardrail):
    # /fleet/* + approvals.
    from graph_os.gateway.fleet import mount_fleet_routes

    mount_fleet_routes(app, prefix=prefix)

    # The operator console's just-in-time elevation approval (EH-405). A plain
    # route, never an MCP tool: no agent can approve through a tool call.
    from graph_os.gateway.elevation import mount_elevation_routes

    mount_elevation_routes(app, prefix=prefix)

    # The operator console's live-order approval and denial (EH-423). Plain
    # routes for the same reason: an agent can propose an order, never decide.
    from graph_os.gateway.finance_orders import mount_finance_order_routes

    mount_finance_order_routes(app, prefix=prefix)

    # Granular, typed, OpenAPI-visible ontology/object reads layered on top of
    # the collapsed action-routed twins (resource-style GET-by-id + history).
    from graph_os.gateway.ontology_api import register_ontology_routes

    register_ontology_routes(app, prefix=prefix)

    # Granular, typed research surface (ARA over the one ontology-driven KG,
    # CONCEPT:AU-KG.research.best-effort-lightweight-never/2.80) — dispatches through
    # the same research_artifact MCP tool.
    from graph_os.gateway.research_api import register_research_routes

    register_research_routes(app, prefix=prefix)

    # Clean-break browser projection over the same typed authorities: current
    # AgentComponent entries plus GraphOS workflow/agent catalogs. WebUI owns
    # no fallback routes or filesystem discovery.
    from graph_os.gateway.enhanced_catalog_api import (
        register_enhanced_catalog_routes,
    )

    register_enhanced_catalog_routes(app, prefix=prefix)

    # Browser OAuth callbacks are part of the same authenticated gateway
    # surface; pass the graph prefix explicitly so providers register the
    # exact deployed callback path without accidental double-prefixing.
    from graph_os.gateway.remote_oauth_api import register_remote_oauth_routes

    register_remote_oauth_routes(app, prefix=prefix)

    logger.info(
        "Mounted centralized Knowledge Graph REST routes + fleet supervisory "
        "plane under %r (graph-os MCP is now a thin wrapper).",
        prefix,
    )
