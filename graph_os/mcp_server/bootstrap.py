"""Engine authority, hydration, and bootstrap lifecycle for graph-os."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

# Bound by ``runtime`` after this module's definitions load. Keeping the host
# state injection explicit avoids an eager bootstrap↔runtime import cycle.
REGISTERED_TOOLS = cast(Any, None)
_AGENT_ID = cast(Any, None)
_AUTHORITY_KEEPALIVE_ACTIVE = cast(Any, None)
_ENGINE_LOCK = cast(Any, None)
_PROCESS_AUTHORITY_STOP = cast(Any, None)
_PROCESS_AUTHORITY_THREAD = cast(Any, None)
_PROCESS_SESSION = cast(Any, None)
_PROCESS_SESSION_REFRESH_LOCK = cast(Any, None)
_SESSION_ID = cast(Any, None)
_TABULAR_QUERY_SERVICE_LOCK = cast(Any, None)
_TABULAR_QUERY_SERVICE = cast(Any, None)
_TABULAR_QUERY_SERVICE_KEY = cast(Any, None)
get_existing_disabled_batch = cast(Any, None)
setting = cast(Any, None)


def _trino_principal_ref() -> str:
    """Resolve the current verified caller's opaque persistence reference."""

    from agent_utilities.security.brain_context import current_actor
    from agent_utilities.security.persistence_privacy import persistence_reference

    actor = current_actor()
    actor.ensure_credential_current()
    if not actor.authenticated or not str(actor.actor_id or "").strip():
        raise PermissionError("verified Trino principal identity is unavailable")
    return persistence_reference("delegated_actor", actor.actor_id)


def _trino_composition_settings() -> tuple[str, str, str]:
    """Read and validate the process-owned Trino composition settings."""

    from agent_utilities.core.config import config as runtime_config

    if not runtime_config.enable_delegation:
        raise RuntimeError("Trino queries require delegated authentication")
    endpoint = str(runtime_config.trino_endpoint or "").strip()
    catalog = str(runtime_config.lakekeeper_warehouse or "").strip()
    audience = str(runtime_config.delegation_audience or "").strip()
    if not endpoint:
        raise RuntimeError("Trino endpoint is not configured (TRINO_ENDPOINT)")
    if not catalog:
        raise RuntimeError("Trino catalog is not configured (LAKEKEEPER_WAREHOUSE)")
    if not audience:
        raise RuntimeError("Trino delegation audience is not configured (AUDIENCE)")
    return endpoint, catalog, audience


def get_tabular_query_service() -> Any:
    """Compose the one tabular query application service for MCP/REST callers.

    Process configuration is bound here, above the backend and application
    layers. Credential providers remain request-scoped callables and are
    resolved by the backend on every dispatch.
    """

    from agent_utilities.knowledge_graph.backends.trino_backend import (
        TrinoQueryBackend,
    )
    from agent_utilities.knowledge_graph.core.tabular_query_service import (
        TabularQueryService,
    )
    from agent_utilities.mcp.delegated_auth import get_delegated_token

    key = _trino_composition_settings()
    global _TABULAR_QUERY_SERVICE, _TABULAR_QUERY_SERVICE_KEY
    previous: Any = None
    with _TABULAR_QUERY_SERVICE_LOCK:
        if _TABULAR_QUERY_SERVICE is not None and _TABULAR_QUERY_SERVICE_KEY == key:
            return _TABULAR_QUERY_SERVICE
        endpoint, catalog, audience = key
        backend = TrinoQueryBackend(
            endpoint,
            catalog=catalog,
            token_provider=lambda: get_delegated_token(audience=audience),
            principal_ref_provider=_trino_principal_ref,
        )
        service = TabularQueryService(backend)
        previous = _TABULAR_QUERY_SERVICE
        _TABULAR_QUERY_SERVICE = service
        _TABULAR_QUERY_SERVICE_KEY = key
    if previous is not None:
        try:
            previous.close()
        except Exception:  # noqa: BLE001 - replacement is already fail-closed
            logger.warning("previous tabular query service close failed", exc_info=True)
    return service


_EXTRACTION_MANAGER: Any = None


def _get_extraction_manager(engine: Any) -> Any:
    """Lazily build the single GPU-slot extraction job manager (KG-2.65)."""
    global _EXTRACTION_MANAGER
    if _EXTRACTION_MANAGER is None:
        from agent_utilities.knowledge_graph.extraction.job_manager import (
            ExtractionJobManager,
        )

        _EXTRACTION_MANAGER = ExtractionJobManager(engine)
    return _EXTRACTION_MANAGER


def _bind_ontology_package_sync(value: Any) -> None:
    """Bind the current ontology adapter once per engine instance."""
    from agent_utilities.mcp.tools.ontology_tools import _sync_package_ontologies

    if getattr(value, "_ontology_package_sync", None) is not _sync_package_ontologies:
        value._ontology_package_sync = _sync_package_ontologies


def _bind_mcp_probe_port(value: Any) -> None:
    """Bind canonical multiplexer/config/async seams for KG MCP consumers."""
    from agent_utilities.knowledge_graph.core.engine_mcp_discovery import MCPProbePort
    from agent_utilities.protocols.source_connectors.connectors.mcp_package import (
        _run_async,
    )

    from graph_os.fleet.multiplexer import MCPMultiplexer, _resolve_config_path

    port = MCPProbePort(
        probe_declaration=MCPMultiplexer.probe_declaration,
        resolve_config_path=_resolve_config_path,
        multiplexer_factory=MCPMultiplexer,
        run_async=_run_async,
    )
    if getattr(value, "mcp_probe_port", None) != port:
        value.mcp_probe_port = port


_RuntimeAuthorityBinder = Callable[[Any], object]


def _runtime_authority_binders() -> tuple[_RuntimeAuthorityBinder, ...]:
    """Return the available process-owned runtime binders."""
    binders: tuple[_RuntimeAuthorityBinder, ...] = (
        _bind_ontology_package_sync,
        _bind_mcp_probe_port,
    )
    try:
        from agent_utilities.mcp.tools.data_prep_tools import (
            register_process_data_prep_runtime,
        )
    except Exception:  # noqa: BLE001 - optional adapters must not block boot
        logger.warning("data prep runtime registration deferred", exc_info=True)
    else:
        return (register_process_data_prep_runtime, *binders)
    return binders


def _bind_runtime_authorities(
    value: Any, binders: tuple[_RuntimeAuthorityBinder, ...]
) -> Any:
    """Bind independent runtime capabilities without suppressing later ones."""
    for binder in binders:
        try:
            binder(value)
        except Exception:  # noqa: BLE001 - optional adapters must not block boot
            logger.warning("runtime capability registration deferred", exc_info=True)
    return value


def _get_engine():
    """Lazily initialize and return the IntelligenceGraphEngine singleton.

    Thread-safe double-checked locking prevents concurrent runtime callers from
    racing a second authority into existence. Direct GraphOS startup resolves
    this engine synchronously only through the bounded packaged-skill readiness
    barrier; noncritical bootstrap work remains asynchronous.
    (CONCEPT:EG-KG.storage.nonblocking-checkpoint)
    """
    from agent_utilities.core.paths import ensure_dirs
    from agent_utilities.knowledge_graph.backends import create_backend
    from agent_utilities.knowledge_graph.core.engine import IntelligenceGraphEngine

    def _register_runtime_authorities(value: Any) -> Any:
        # Registration is process-owned startup state.  The served callers can
        # only resolve these recorded adapters, never select one from request
        # data. Each optional binder is isolated so one failure cannot suppress
        # the remaining capabilities.
        return _bind_runtime_authorities(value, _runtime_authority_binders())

    engine = IntelligenceGraphEngine.get_active()
    if engine is not None:
        return _register_runtime_authorities(engine)

    with _ENGINE_LOCK:
        engine = IntelligenceGraphEngine.get_active()
        if engine is not None:
            return _register_runtime_authorities(engine)
        # First-run: ensure XDG dirs exist and create backend
        ensure_dirs()

        def _factory():
            backend = create_backend()
            return IntelligenceGraphEngine(
                backend=backend,
                defer_background_start=True,
            )

        return _register_runtime_authorities(
            IntelligenceGraphEngine.get_or_create(factory=_factory)
        )


# ── CONCEPT:AU-KG.backend.multi-connection-registry — Named multi-connection graph registry ────────────────
_CONNECTION_REGISTRY = None
_REGISTRY_LOCK = threading.Lock()


def get_connection_registry():
    """Process-wide :class:`ConnectionRegistry` singleton.

    The reserved ``"default"`` target resolves only the process-active authority;
    registry construction never creates or seeds a second engine. Reference-only
    ``config.external_graph_connectors`` and ``config.kg_connections`` are
    registered on first build.
    """
    global _CONNECTION_REGISTRY
    if _CONNECTION_REGISTRY is not None:
        return _CONNECTION_REGISTRY
    with _REGISTRY_LOCK:
        if _CONNECTION_REGISTRY is not None:
            return _CONNECTION_REGISTRY
        from agent_utilities.knowledge_graph.core.connection_registry import (
            ConnectionRegistry,
        )

        registry = ConnectionRegistry()
        # Seed reference-only external sources first, then let an explicit
        # KG_CONNECTIONS declaration with the same alias take precedence.
        _seed_connection_registry(registry)
        _CONNECTION_REGISTRY = registry
        return _CONNECTION_REGISTRY


def _seed_external_graph_connectors(registry: Any) -> None:
    """Register reference-only external sources from config, best-effort per item."""
    from agent_utilities.core.config import config as _cfg

    for declared in _cfg.external_graph_connectors or []:
        value = (
            declared.model_dump() if hasattr(declared, "model_dump") else dict(declared)
        )
        name = str(value.pop("name", "") or "")
        if not name:
            continue
        value["role"] = "read"
        try:
            registry.register(name, value)
        except Exception as exc:  # noqa: BLE001 — one bad declaration never blocks the rest
            logger.warning(
                "Skipping invalid external source declaration: %s",
                type(exc).__name__,
            )


def _seed_kg_connections(registry: Any) -> None:
    """Register explicit KG_CONNECTIONS declarations from config, best-effort per item."""
    from agent_utilities.core.config import config as _cfg

    for spec in _cfg.kg_connections or []:
        spec = dict(spec)
        name = spec.pop("name", "")
        if name:
            try:
                registry.register(name, spec)
            except Exception as e:  # noqa: BLE001 — one bad declaration never blocks the rest
                logger.warning(
                    "Skipping invalid graph connection declaration: %s",
                    type(e).__name__,
                )


def _seed_connection_registry(registry: Any) -> None:
    """Seed a fresh :class:`ConnectionRegistry` from config, best-effort.

    An explicit ``KG_CONNECTIONS`` declaration takes precedence over a
    reference-only external source registered under the same alias, since
    external sources are seeded first. Config-less environments (the whole
    seeding step raises) leave the registry with nothing seeded.
    """
    try:
        _seed_external_graph_connectors(registry)
        _seed_kg_connections(registry)
    except Exception as exc:  # noqa: BLE001 — config-less environments
        logger.debug(
            "Graph connection declarations were not seeded (%s)",
            type(exc).__name__,
        )


def _resolve_target_engines(
    target: Any,
) -> tuple[list[tuple[str, Any]], dict[str, str], bool]:
    """Resolve a tool ``target`` into live engines for execution.

    Returns ``(entries, errors, fanout)`` where ``entries`` is a list of
    ``(name, engine)`` to run against and ``errors`` maps any name that could not
    be resolved to its error string. For a non-fan-out target, resolution errors
    propagate (fail-loud); for fan-out they are captured into ``errors`` so one
    bad connection never aborts the others (partial-success contract).
    """
    registry = get_connection_registry()
    names, fanout = registry.resolve_names(target)
    entries: list[tuple[str, Any]] = []
    errors: dict[str, str] = {}
    for name in names:
        if fanout:
            engine, err = registry.safe_get_engine(name)
            if err is not None:
                errors[name] = err
            else:
                entries.append((name, engine))
        else:
            entries.append((name, registry.get_engine(name)))
    return entries, errors, fanout


def _resolve_read_engines(
    target: Any,
) -> tuple[list[tuple[str, Any]], dict[str, str], bool]:
    """Resolve a READ tool's ``target`` into engines, unioning content graphs.

    CONCEPT:AU-KG.ingest.unified-query-routing — preserve unified query under ingestion graph routing. When
    routing is on and the caller did NOT pin an explicit target, content lives
    spread across per-source graphs (``code:*`` / ``src:*`` / …) that the single
    default engine cannot see. This resolver returns one engine per active content
    graph (plus the default) with ``fanout=True``, so the existing fan-out machinery
    unions them and a node written to ``code:X`` stays findable via the normal
    ``graph_search`` / ``graph_query`` path. An explicit ``target`` (a named
    connection, ``"all"``, a list) defers to the standard connection resolver
    unchanged, and with routing off this is byte-for-byte ``_resolve_target_engines``.
    """
    from agent_utilities.knowledge_graph.core import ingest_routing

    is_implicit_default = target is None or (
        isinstance(target, str) and target.strip().lower() in ("", "default")
    )
    if not is_implicit_default:
        return _resolve_target_engines(target)

    read_graphs = ingest_routing.read_graph_targets()
    if len(read_graphs) <= 1:
        # Nothing routed yet → stay on the fast single default-graph path.
        return _resolve_target_engines(target)

    from agent_utilities.knowledge_graph.core.shard_topology import default_graph_name

    default_graph = default_graph_name()
    entries: list[tuple[str, Any]] = []
    errors: dict[str, str] = {}
    # CONCEPT:AU-KG.backend.fanout-dedup — de-duplicate fan-out targets by the engine's actual bound
    # graph so the SAME backend (e.g. ``__commons__``) is never queried more than
    # once. Without this a query for nodes that live only in the default graph is
    # answered identically by every target, and an aggregation row (no node id to
    # dedup on) is repeated once per graph. Key on the backend's ``graph_name``,
    # falling back to ``id(engine)`` so two engines over one store collapse to one.
    seen_backends: set[Any] = set()

    def _backend_key(engine: Any) -> Any:
        gname = getattr(getattr(engine, "backend", None), "graph_name", None)
        return gname if gname is not None else id(engine)

    for gname in read_graphs:
        if gname == default_graph:
            eng: Any = _get_engine()
            name = "default"
        else:
            eng, err = ingest_routing.safe_engine_for_graph(gname)
            if err is not None:
                errors[gname] = err
                continue
            name = gname
        key = _backend_key(eng)
        if key in seen_backends:
            continue
        seen_backends.add(key)
        entries.append((name, eng))
    return entries, errors, True


class GraphSelectionError(ValueError):
    """Base for an explicit ``graph`` selection that cannot be honored.

    CONCEPT:AU-KG.backend.explicit-graph-selection — ``connection`` (a backend
    alias, CONCEPT:AU-KG.backend.multi-connection-registry, resolved by
    :class:`~agent_utilities.knowledge_graph.core.connection_registry.ConnectionRegistry`)
    and ``graph`` (a physical engine graph, the thing ``engine_tenants(action="list")``/
    the engine's own ``ListGraphs`` returns) are two independent axes. Every raise
    of this family is FAIL-CLOSED: the caller gets a typed error, never a silent
    fallback to a default graph and never a union across graphs.
    """


class GraphNotFoundError(GraphSelectionError, LookupError):
    """The requested ``graph`` is absent from the engine's own catalog."""


class GraphSelectionConflictError(GraphSelectionError):
    """The ``graph``/``connection`` combination is ambiguous or unsupported.

    Raised instead of silently picking one interpretation — e.g. an explicit
    ``graph`` alongside a fan-out ``connection`` (``'all'``/a list), or an
    explicit ``graph`` alongside a non-native (external/read-only) connection
    that has no physical-graph concept of its own.
    """


def resolve_explicit_graph(
    entries: list[tuple[str, Any]], graph: str, *, fanout: bool
) -> list[tuple[str, Any]]:
    """Validate an explicit ``graph`` against the already-resolved connection(s).

    Returns ``entries`` unchanged (no-op) when ``graph`` is empty — existing
    ``connection``-only behavior is untouched. When ``graph`` is set:

    * a fan-out ``connection`` (``entries`` has more than one, or ``fanout`` is
      True) is rejected — an explicit graph requires exactly one connection,
      never a union (CONCEPT:AU-KG.backend.explicit-graph-selection).
    * a non-``"default"`` connection is rejected — only the native (epistemic-
      graph) connection has a multi-graph concept; naming a graph for e.g. an
      external Neo4j connection would otherwise be silently ignored (a silent
      pick of that connection's own implicit graph), which is exactly the
      defect this function exists to prevent.
    * existence is checked, best-effort, against the SAME catalog
      ``engine_tenants(action="list")``/``ListGraphs`` already exposes
      (``engine.graph_compute.client.tenants.list()``) — never a parallel
      authorization mechanism. The actual per-call authorization remains the
      engine's own RBAC/RLS (``crates/eg-core/src/isolation.rs::check_access``),
      evaluated server-side on every request this binds into, regardless of
      whether this probe ran.
    """
    graph = graph.strip() if isinstance(graph, str) else ""
    if not graph:
        return entries
    if fanout or len(entries) != 1:
        raise GraphSelectionConflictError(
            "an explicit graph requires exactly one resolved connection, "
            "never a fan-out/union"
        )
    name, engine = entries[0]
    if name != "default":
        raise GraphSelectionConflictError(
            f"connection {name!r} has no physical-graph concept; explicit "
            "graph selection is supported only on the default connection"
        )
    _validate_graph_exists_in_catalog(engine, graph)
    return entries


def _validate_graph_exists_in_catalog(engine: Any, graph: str) -> None:
    """Best-effort existence check for ``graph`` against the engine's catalog.

    A degraded/unavailable catalog probe must never itself deny or (worse)
    silently permit — the engine's own RBAC/RLS is the real authorization
    boundary either way, so this just skips the check in that case and lets
    the actual call surface whatever the engine decides.
    """
    tenants = getattr(
        getattr(getattr(engine, "graph_compute", None), "client", None),
        "tenants",
        None,
    )
    list_graphs = getattr(tenants, "list", None)
    if not callable(list_graphs):
        return
    try:
        catalog = list_graphs() or []
    except Exception:  # noqa: BLE001 — best-effort probe; see docstring
        return
    names = {
        row.get("name") for row in catalog if isinstance(row, dict) and row.get("name")
    }
    if graph not in names:
        raise GraphNotFoundError(
            f"graph {graph!r} is not present in the engine catalog"
        )


@contextlib.contextmanager
def bound_to_graph(graph: str) -> Any:
    """Bind an explicit physical graph onto the verified session for one call.

    A no-op when ``graph`` is empty. Otherwise reuses the SAME sanctioned
    session-retargeting primitive already used by
    ``agent_utilities.knowledge_graph.pipeline`` and
    ``agent_utilities.orchestration.approval`` —
    :meth:`~agent_utilities.knowledge_graph.core.session.GraphSession.with_graph`
    scoped for the call's duration via
    :func:`~agent_utilities.knowledge_graph.core.session.use_session`. This
    introduces no new authority: the engine's own RBAC/RLS
    (``crates/eg-core/src/isolation.rs::check_access``) evaluates
    ``(agent_id, graph, action)`` on every RPC issued while bound, independent
    of what this function does — binding only says which graph the request
    NAMES, never what it is ALLOWED to touch.
    """
    graph = graph.strip() if isinstance(graph, str) else ""
    if not graph:
        yield
        return
    from agent_utilities.knowledge_graph.core.session import (
        current_session,
        use_session,
    )

    session = current_session()
    if session is None:
        raise GraphSelectionConflictError(
            "a verified session is required to bind an explicit graph"
        )
    with use_session(session.with_graph(graph)):
        yield


#: Per-target wall-clock budget (seconds) for a fan-out (``target='all'`` or a
#: multi-target list). One slow/unreachable backend must not stall the whole set;
#: override live via ``graph_configure set_config GRAPH_FANOUT_TIMEOUT`` (KG-2.63).
DEFAULT_FANOUT_TIMEOUT_S = 30.0

#: Per-target wall-clock budget (seconds) for an IMPLICIT-default read fan-out —
#: a ``graph_search``/``graph_query`` call with no explicit ``target`` that
#: resolves (CONCEPT:AU-KG.ingest.unified-query-routing) to the routed
#: content-graph union: ``default`` + every active ``code:*``/``src:*`` graph,
#: which can be dozens of per-repo connections and often includes idle/
#: unreachable ones. Using the full ``DEFAULT_FANOUT_TIMEOUT_S`` there means one
#: unreachable ``code:<repo>`` backend blocks the common no-target call for up to
#: 30s each, flooding the result with "timed out" entries. A short budget keeps
#: the default call fast — an unreachable graph is skipped, not waited on — while
#: an explicit ``target='all'``/list (a deliberate cross-repo search) keeps the
#: full ``DEFAULT_FANOUT_TIMEOUT_S``.
DEFAULT_CONTENT_FANOUT_TIMEOUT_S = 3.0


def fanout_error_label(exc: BaseException) -> str:
    """Classify a per-target fan-out failure as retryable-degraded or rejected.

    BUG-048: ``fanout_execute`` (below) and the "primary target" call sites
    that deliberately bypass its concurrency machinery but must mirror its
    error handling (e.g. ``graph_query``'s ``default``-connection fast path
    in ``query_tools.py``) reduce a raised exception to a bare string outside
    the ``OperationResult``/``OperationError`` schema ``public_error_payload``
    covers. Without this, a breaker-open on one fan-out target collapsed to
    the SAME ``"target_operation_failed"`` label as a genuinely rejected
    query — indistinguishable to a caller deciding whether to retry, exactly
    the defect BUG-048 fixed at the single-target boundary. Reuses
    :func:`agent_utilities.security.error_surface.is_engine_degraded` — the
    one place this breaker-vs-rejection vocabulary is defined — rather than
    reimplementing it a third time.
    """
    from agent_utilities.security.error_surface import is_engine_degraded

    return "target_degraded" if is_engine_degraded(exc) else "target_operation_failed"


def fanout_execute(entries, fn, *, timeout=None):
    """Run ``fn(name, engine)`` for every fan-out target CONCURRENTLY under a shared
    per-target wall-clock timeout, so one slow/unreachable backend can't stall the
    others (CONCEPT:AU-KG.backend.multi-connection-registry).

    Returns ``(results, errors)`` keyed by connection name. A target that exceeds the
    budget (or raises) lands in ``errors`` while the rest still return — the
    partial-success contract the sequential loop violated by blocking on the slowest.
    A raised target is labeled via :func:`fanout_error_label` — ``"target_degraded"``
    for a breaker-open/transport-down failure (retryable), ``"target_operation_failed"``
    for everything else (BUG-048) — never the exception text itself.

    B-18: ``concurrent.futures.ThreadPoolExecutor.submit`` does NOT propagate the
    calling thread's :mod:`contextvars` context — unlike ``asyncio.to_thread``
    (which the async tool endpoints above use to reach this synchronous helper
    in the first place), a plain worker thread starts with a FRESH, empty
    context. Without this, the ambient authenticated ``GraphSession`` (and any
    narrowing a caller applies via ``bound_to_graph`` around ``fn``) is
    invisible inside every fan-out target, and each one fails closed with
    ``SessionRequiredError`` regardless of which graph it targets. Capturing
    ``contextvars.copy_context()`` once PER submission (never reused across
    concurrent ``Context.run`` calls, which is not reentrant) and running
    ``fn`` inside that copy restores the ambient session per target while
    keeping each target's own narrowing (if any) isolated from its siblings.
    """
    import concurrent.futures

    if timeout is None:
        timeout = float(setting("GRAPH_FANOUT_TIMEOUT", DEFAULT_FANOUT_TIMEOUT_S))
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if not entries:
        return results, errors
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(entries)))
    futures = {
        ex.submit(contextvars.copy_context().run, fn, name, engine): name
        for name, engine in entries
    }
    done, not_done = concurrent.futures.wait(futures, timeout=timeout)
    for fut in done:
        name = futures[fut]
        try:
            results[name] = fut.result()
        except Exception as exc:  # noqa: BLE001 — partial-success contract
            logger.warning(
                "Graph fan-out target failed (exception_type=%s)",
                type(exc).__name__,
            )
            errors[name] = fanout_error_label(exc)
    for fut in not_done:
        errors[futures[fut]] = "target_timeout"
    # Never block on a hung backend's thread; let it finish in the background.
    ex.shutdown(wait=False, cancel_futures=True)
    return results, errors


def _provenance_props(agent_id: str | None = None) -> dict[str, Any]:
    """Build persistence-safe provenance without host or principal material."""
    from agent_utilities.security.persistence_privacy import persistence_reference

    return {
        "agent_ref": persistence_reference(
            "agent", agent_id or _AGENT_ID, namespace="mcp-provenance"
        ),
        "session_ref": persistence_reference(
            "session", _SESSION_ID, namespace="mcp-provenance"
        ),
        "timestamp": datetime.now(UTC).isoformat(),
        "source": "mcp",
    }


def _neutral_capability_name(value: object, *, fallback_ref: str) -> str:
    """Return a bounded service alias, never an arbitrary config key."""
    from agent_utilities.security.persistence_privacy import sanitize_for_persistence

    rendered = str(value or "").strip().lower()
    sanitized, report = sanitize_for_persistence(rendered)
    if not report.changed and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", rendered):
        return rendered
    return f"external-{fallback_ref.rsplit('_', 1)[-1][:12]}"


def _mcp_capability_declaration(
    server_name: object, server_details: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Project one MCP runtime declaration into privacy-safe KG metadata."""
    from agent_utilities.knowledge_graph.core.source_sync import (
        derive_capability_synonyms,
    )
    from agent_utilities.security.persistence_privacy import persistence_reference

    server_ref = persistence_reference(
        "mcp_server", server_name, namespace="capability-ingestion"
    )
    neutral_name = _neutral_capability_name(server_name, fallback_ref=server_ref)
    configuration_ref = persistence_reference(
        "mcp_configuration",
        json.dumps(server_details, sort_keys=True, separators=(",", ":")),
        namespace=server_ref,
    )
    capabilities = [
        str(value).lower()
        for value in server_details.get("capabilities", [])
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", str(value))
    ]
    return (
        f"mcp_server:{server_ref}",
        {
            "name": neutral_name,
            "server_ref": server_ref,
            "configuration_ref": configuration_ref,
            "capabilities": sorted(set(capabilities)),
            "synonyms": derive_capability_synonyms(neutral_name),
        },
    )


def _ontology_system():
    """Return an OntologySystem bound to the live engine store (or offline).

    Module-level so the ontology/object tool group registers from
    mcp/tools/ontology_tools.py instead of a _build_server closure.
    """
    from agent_utilities.knowledge_graph.facade import KnowledgeGraph

    try:
        engine = _get_engine()
    except Exception:  # pragma: no cover - defensive
        engine = None
    backend = getattr(engine, "backend", None) if engine is not None else None
    kg = KnowledgeGraph()
    if backend is not None:
        kg._store = backend
    return kg.ontology


class GraphOSStartupReadinessError(RuntimeError):
    """Stable, environment-free failure raised before GraphOS starts serving."""


def _read_skill_capability(skill_md) -> tuple[str, str, str, str | None]:
    """Read one bounded skill declaration without retaining its discovery path.

    Returns ``(name, description, instructions, skill_type)`` — ``skill_type``
    is the frontmatter's own ``skill_type`` declaration (or ``None`` when
    absent), threaded through to :func:`~agent_utilities.knowledge_graph.
    ingestion.skill_workflow_ingest.ingest_runnable_skill` so classification is
    a stored column rather than a value dropped on the floor at read time
    (CONCEPT:AU-KG.ingest.fleet-catalog-relational-tables).
    """
    path = Path(skill_md)
    payload = path.read_bytes()
    if not payload or len(payload) > 512 * 1024:
        raise ValueError("skill declaration size is invalid")
    content = payload.decode("utf-8")
    frontmatter, instructions = _parse_skill_capability_frontmatter(content)
    fallback_name = path.parent.name
    name = str(frontmatter.get("name") or fallback_name).strip()
    description = str(frontmatter.get("description") or "").strip()
    raw_skill_type = frontmatter.get("skill_type")
    skill_type = str(raw_skill_type).strip().lower() or None if raw_skill_type else None
    if not name or not instructions.strip():
        raise ValueError("skill declaration is incomplete")
    return name, description, instructions, skill_type


def _parse_skill_capability_frontmatter(content: str) -> tuple[dict, str]:
    """Split a skill declaration's YAML frontmatter from its instructions body.

    Returns ``(frontmatter, instructions)``. When there is no ``---``-delimited
    frontmatter block, ``frontmatter`` is empty and ``instructions`` is the
    whole content, unchanged.
    """
    import yaml

    frontmatter: dict = {}
    instructions = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            parsed = yaml.safe_load(parts[1].strip()) or {}
            if not isinstance(parsed, dict):
                raise ValueError("skill frontmatter must be an object")
            frontmatter = parsed
            instructions = parts[2].strip()
    return frontmatter, instructions


def _ingest_skill_capabilities(
    engine,
    provider: str,
    skills_path,
    *,
    include_names: frozenset[str] | None = None,
    skip_names: frozenset[str] = frozenset(),
) -> int:
    """Persist provider skills as runnable resources without retaining paths.

    The prior-``disabled`` lookup used to be one ``get_existing_disabled``
    engine round trip PER skill file inside this loop — for the full fleet
    skill catalog (hundreds of ``SKILL.md`` files under
    ``resolve_skill_provider_dirs()``) that is exactly the per-element engine
    call this codebase's own design rule forbids ("batch, never per-element;
    N elements in a loop = N round-trips = catastrophic" — see
    ``epistemic-graph`` AGENTS.md). It was the dominant contributor to the
    measured GraphOS cold-boot incident (2026-08-16): hundreds of sequential
    ``HasNode``/``GetNodeProperties``/``BatchUpdate`` round trips against a
    contended engine, each taking 1-15s under load. Every candidate's local
    ``SKILL.md`` is now parsed first (cheap, no engine call), then the
    "already ingested / disabled" state for the WHOLE batch is resolved with
    ONE :func:`get_existing_disabled_batch` call before any per-skill write.
    """
    from agent_utilities.core.providers import is_skill_graph_reference_path

    root = Path(skills_path)
    if not root.is_dir():
        return 0

    skill_files = (
        [root / "SKILL.md"]
        if (root / "SKILL.md").is_file()
        else sorted(
            skill_md
            for skill_md in root.rglob("SKILL.md")
            if not is_skill_graph_reference_path(skill_md, root)
        )
    )

    declarations = _collect_skill_declarations(
        skill_files, include_names=include_names, skip_names=skip_names
    )
    if not declarations:
        return 0

    disabled_by_resource = get_existing_disabled_batch(
        engine, [declaration[4] for declaration in declarations]
    )

    total = len(declarations)
    # Make an in-progress boot pass observable: this loop was previously
    # indistinguishable, in the container logs, from a hung process — an
    # operator saw only individual engine-op trace lines with no running
    # count or total, exactly the ambiguity that turned the 2026-08-16
    # cold-start incident into an 11-minute unattributed stall before the
    # startup probe killed the container. A bounded item count up front plus
    # a periodic "N/total" line lets "still working" be told apart from
    # "stuck" without reading engine wire traces.
    logger.info("GraphOS ingesting %d %s skill(s) from %s", total, provider, root)
    return _write_skill_declarations(
        engine,
        declarations,
        provider=provider,
        disabled_by_resource=disabled_by_resource,
    )


def _collect_skill_declarations(
    skill_files: list[Path],
    *,
    include_names: frozenset[str] | None,
    skip_names: frozenset[str],
) -> list[tuple[Path, str, str, str, str, str | None]]:
    """Parse candidate ``SKILL.md`` files into declarations, skipping bad ones.

    A malformed skill's parse failure is logged (stage="declaration") and
    that skill excluded — never blocks the batch (see
    :func:`_ingest_skill_capabilities`'s docstring for why this is a
    separate pass from the write loop).
    """
    from agent_utilities.knowledge_graph.ingestion.skill_workflow_ingest import (
        skill_reference,
    )
    from agent_utilities.security.persistence_privacy import persistence_reference

    declarations: list[tuple[Path, str, str, str, str, str | None]] = []
    for skill_md in skill_files:
        fallback_name = skill_md.parent.name
        try:
            name, description, instructions, skill_type = _read_skill_capability(
                skill_md
            )
            if include_names is not None and name not in include_names:
                continue
            if name in skip_names:
                continue
            skill_slug = skill_reference(name).removeprefix("skill://")
            resource_id = f"resource:skill:{skill_slug}"
            declarations.append(
                (skill_md, name, description, instructions, resource_id, skill_type)
            )
        except Exception as exc:  # noqa: BLE001 - one malformed skill cannot block boot
            # ``exc.args[0]`` (not ``str(exc)``/``exc`` itself, and no
            # ``exc_info=True``) preserves the real cause for the operator
            # (test_boot_skill_failure_log_uses_neutral_reference) while
            # satisfying the served-boundary exception-surface gate. The
            # skill's discovery PATH is never in this message (``_read_
            # skill_capability`` raises path-free ValueErrors/YAML parser
            # errors), and the skill's own identity is already redacted via
            # ``persistence_reference`` above.
            logger.error(
                "Failed to ingest %s (stage=%s %s: %s)",
                persistence_reference(
                    "skill", fallback_name, namespace="skill-provider-ingest"
                ),
                "declaration",
                type(exc).__name__,
                exc.args[0] if exc.args else "",
            )
    return declarations


def _write_skill_declarations(
    engine,
    declarations: list[tuple[Path, str, str, str, str, str | None]],
    *,
    provider: str,
    disabled_by_resource: dict[str, bool],
) -> int:
    """Write each parsed skill declaration as a runnable resource.

    Logs an "N/total" progress line periodically (see
    :func:`_ingest_skill_capabilities`'s docstring for why). A malformed or
    failing write is logged (stage="write") and skipped — never blocks the
    batch.
    """
    from agent_utilities.knowledge_graph.ingestion.skill_workflow_ingest import (
        ingest_runnable_skill,
    )
    from agent_utilities.security.persistence_privacy import persistence_reference

    total = len(declarations)
    ingested = 0
    for index, (
        skill_md,
        name,
        description,
        instructions,
        resource_id,
        skill_type,
    ) in enumerate(declarations, start=1):
        fallback_name = skill_md.parent.name
        try:
            ingest_runnable_skill(
                engine,
                name=name,
                description=description,
                instructions=instructions,
                provider=provider,
                disabled=disabled_by_resource.get(resource_id, False),
                skill_type=skill_type,
            )
            ingested += 1
            if index % 25 == 0 or index == total:
                logger.info(
                    "GraphOS skill ingestion progress: %d/%d (%s)",
                    index,
                    total,
                    provider,
                )
        except Exception as exc:  # noqa: BLE001 - one malformed skill cannot block boot
            logger.error(
                "Failed to ingest %s (stage=%s %s: %s)",
                persistence_reference(
                    "skill", fallback_name, namespace="skill-provider-ingest"
                ),
                "write",
                type(exc).__name__,
                exc.args[0] if exc.args else "",
            )
    return ingested


def _bundled_skill_contract() -> tuple[Path, dict[str, str]]:
    """Load the exact current packaged-skill digest contract."""
    from agent_utilities.knowledge_graph.ingestion.skill_workflow_ingest import (
        runnable_skill_digest,
    )
    from agent_utilities.security.persistence_privacy import PersistencePrivacyGuard
    from agent_utilities.skills import BUNDLED_SKILLS

    if len(BUNDLED_SKILLS) != 13 or len(set(BUNDLED_SKILLS)) != 13:
        raise GraphOSStartupReadinessError("graphos_bundled_skills_unready")
    root = Path(__file__).resolve().parents[1] / "skills"
    guard = PersistencePrivacyGuard()
    expected: dict[str, str] = {}
    try:
        for bundled_name in BUNDLED_SKILLS:
            name, _description, instructions, _skill_type = _read_skill_capability(
                root / bundled_name / "SKILL.md"
            )
            if name != bundled_name:
                raise ValueError("bundled skill identity mismatch")
            body, _privacy = guard.sanitize_text(instructions.strip())
            if not body:
                raise ValueError("bundled skill body is empty")
            expected[bundled_name] = runnable_skill_digest(body)
    except Exception as exc:
        # Only the exception type is recorded in the log (never its raw message
        # or traceback, D-LR-2); the real cause still propagates to the caller
        # via the chained `from exc` on the re-raise below.
        logger.error(
            "GraphOS packaged-skill readiness check failed (%s)",
            type(exc).__name__,
        )
        raise GraphOSStartupReadinessError("graphos_bundled_skills_unready") from exc
    return root, expected


def _query_bundled_skill_rows(
    engine: Any, expected_digests: dict[str, str]
) -> list[dict[str, Any]] | None:
    """Run the bundled-skill readiness probe query.

    Returns ``None`` (never raises) when the probe cannot run at all, or on
    a graph that does not exist yet — a first boot, or the first boot after
    a tenant claim starts scoping this process to a new tenant graph, where
    the engine answers "Graph '<name>' not found" rather than an empty
    result. That is the correct answer to "nothing is ready", not a
    failure, and treating it as fatal makes the server unable to perform
    the very ingestion that would create the graph. A genuine engine fault
    still surfaces from the caller's own use of the (empty) result.
    """
    query = getattr(engine, "query_cypher", None)
    if not callable(query):
        return None
    try:
        return query(
            "MATCH (n:CallableResource) WHERE n.name IN $names "
            "RETURN n.id AS id, n.name AS name, n.resource_type AS rtype, "
            "n.system_prompt AS system_prompt, "
            "n.instruction_digest AS instruction_digest, "
            "n.source_ref AS source_ref, n.runnable_bound AS runnable_bound",
            {"names": sorted(expected_digests)},
        )
    except Exception as exc:
        logger.info(
            "bundled-skill readiness probe found no existing skill graph "
            "(%s); treating every bundled skill as not yet ingested",
            exc,
        )
        return None


def _group_bundled_skill_candidates(
    rows: list[dict[str, Any]] | None, expected_digests: dict[str, str]
) -> dict[str, list[dict[str, Any]]]:
    """Bucket readiness-probe rows by skill name, ignoring unrequested names."""
    candidates: dict[str, list[dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name in expected_digests:
            candidates.setdefault(name, []).append(row)
    return candidates


def _bundled_skill_row_matches_contract(
    row: dict[str, Any], name: str, expected_digest: str, expected_ref: str
) -> bool:
    """Does this one candidate row satisfy the exact ready contract for ``name``?"""
    from agent_utilities.knowledge_graph.ingestion.skill_workflow_ingest import (
        runnable_skill_digest,
    )

    body = str(row.get("system_prompt") or "").strip()
    digest = str(row.get("instruction_digest") or "")
    return (
        row.get("id") == f"resource:skill:{name}"
        and row.get("rtype") == "AGENT_SKILL"
        and row.get("runnable_bound") is True
        and row.get("source_ref") == expected_ref
        and bool(body)
        and digest == expected_digest
        and runnable_skill_digest(body) == digest
    )


def _is_bundled_skill_ready(
    name: str, expected_digest: str, matches: list[dict[str, Any]]
) -> bool:
    """Is any candidate row for ``name`` exactly the expected ready contract?"""
    from agent_utilities.knowledge_graph.ingestion.skill_workflow_ingest import (
        skill_reference,
    )

    if len(matches) > 1:
        # Readiness asks "is a correct node present", and every check below
        # pins the exact node id `resource:skill:<name>`, so a second row can
        # never sneak past them. Requiring exactly ONE row instead conflated
        # "more than one row came back" with "not ready", which left a skill
        # permanently unready and — because this is a HARD startup gate —
        # kept graph-os from serving at all. Log the duplication as the
        # hygiene problem it is, then evaluate the rows on their merits.
        logger.warning(
            "bundled skill %r resolved to %d nodes; readiness is decided by "
            "the exact id resource:skill:%s",
            name,
            len(matches),
            name,
        )
    expected_ref = skill_reference(name)
    return any(
        _bundled_skill_row_matches_contract(row, name, expected_digest, expected_ref)
        for row in matches
    )


def _ready_bundled_skill_names(
    engine: Any, expected_digests: dict[str, str]
) -> frozenset[str]:
    """Return exact packaged skills already ready for delegated execution."""
    rows = _query_bundled_skill_rows(engine, expected_digests)
    candidates = _group_bundled_skill_candidates(rows, expected_digests)
    ready = {
        name
        for name, expected_digest in expected_digests.items()
        if _is_bundled_skill_ready(name, expected_digest, candidates.get(name, []))
    }
    return frozenset(ready)


def _ensure_bundled_skills_ready(engine: Any) -> dict[str, Any]:
    """Synchronously establish the packaged delegation contract before serving."""
    from agent_utilities.skills import BUNDLED_SKILLS

    try:
        root, expected = _bundled_skill_contract()
        ready_before = _ready_bundled_skill_names(engine, expected)
        missing = frozenset(BUNDLED_SKILLS) - ready_before
        ingested = 0
        if missing:
            ingested = _ingest_skill_capabilities(
                engine,
                "agent-utilities",
                root,
                include_names=missing,
            )
        ready_after = _ready_bundled_skill_names(engine, expected)
        if ready_after != frozenset(BUNDLED_SKILLS):
            # Name the skills that did not become ready. A bare count tells an
            # operator that something is wrong but not which thing, and this is a
            # HARD startup gate — the difference decides whether the server runs.
            logger.error(
                "GraphOS packaged-skill readiness incomplete "
                "(ready_before=%d ingested=%d ready_after=%d required=%d) "
                "not_ready=%s",
                len(ready_before),
                ingested,
                len(ready_after),
                len(BUNDLED_SKILLS),
                sorted(frozenset(BUNDLED_SKILLS) - ready_after),
            )
    except GraphOSStartupReadinessError:
        raise
    except Exception as exc:
        # The internal agent_utilities.* log is inside the process-wide
        # log_privacy.py sanitization boundary (paths/endpoints/emails
        # redacted, message preserved), so it carries the real exception here
        # for diagnosability. D-LR-2 still holds for the EXTERNAL boundary
        # below: the /health payload has no such sanitizer, so the "error"
        # field there stays type-only.
        logger.error(
            "GraphOS packaged-skill readiness check failed: %s",
            exc,
        )
        return {
            "required": len(BUNDLED_SKILLS),
            "already_ready": 0,
            "ingested": 0,
            "ready": 0,
            "not_ready": sorted(BUNDLED_SKILLS),
            # Unlike the logger.error above (an agent_utilities.* logger,
            # already inside the process-wide privacy boundary), this dict is
            # published via _set_bundled_skill_readiness for the /health HTTP
            # surface (agent_utilities/observability/runtime_health.py's
            # _check_bundled_skills) -- an external caller, so only the
            # exception TYPE is exposed here, never its raw text (D-LR-2).
            "error": type(exc).__name__,
        }
    return {
        "required": len(BUNDLED_SKILLS),
        "already_ready": len(ready_before),
        "ingested": ingested,
        "ready": len(ready_after),
        "not_ready": sorted(frozenset(BUNDLED_SKILLS) - ready_after),
    }


def _ingest_capabilities(engine, *, skip_skill_names: frozenset[str] = frozenset()):
    """Natively ingest MCP configurations, Native Tools, and Skills into the KG on startup."""
    _ingest_mcp_config_capabilities(engine)
    _ingest_native_tool_capabilities(engine)
    _ingest_skill_provider_capabilities(engine, skip_skill_names)

    # Fleet tool schemas stay lazy.  Startup has already materialized each MCP
    # server declaration above; probing every child here would launch the whole
    # fleet and contend with an operator's targeted ``list_catalog`` call.
    # Explicit ``source_sync(source="fleet")`` remains the governed full-scan
    # path when an operator wants every live tool schema elevated into the KG.


def _load_mcp_config_servers() -> dict[str, Any] | None:
    """Read + parse ``mcp_config.json``'s ``mcpServers`` map, or ``None`` if absent.

    Raises on a config file that exists but fails validation (oversized, not
    JSON, or not the expected shape) — the caller's boot-time try/except logs
    and skips this whole ingestion step on any of those.
    """
    import json

    import platformdirs

    APP_NAME = "agent-utilities"
    APP_AUTHOR = "knuckles-team"
    cfg_dir = Path(platformdirs.user_config_path(APP_NAME, APP_AUTHOR))
    mcp_config_path = cfg_dir / "mcp_config.json"
    if not mcp_config_path.is_file() or mcp_config_path.is_symlink():
        return None
    payload = mcp_config_path.read_bytes()
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("MCP configuration exceeds its ingestion bound")
    data = json.loads(payload)
    mcp_servers = data.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError("MCP server registry must be an object")
    return mcp_servers


def _build_mcp_server_declarations(
    mcp_servers: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Build ``(node_id, declaration)`` pairs for every valid server entry."""
    declarations: list[tuple[str, dict[str, Any]]] = []
    for server_name, server_details in mcp_servers.items():
        if not isinstance(server_details, dict):
            continue
        node_id, declaration = _mcp_capability_declaration(server_name, server_details)
        declarations.append((node_id, declaration))
    return declarations


def _ingest_mcp_server_declarations(
    engine: Any, declarations: list[tuple[str, dict[str, Any]]]
) -> int:
    """Batch-resolve prior ``disabled`` state, then write every server node.

    One batched round trip for every server's prior ``disabled`` flag
    instead of one query per server (was the dominant source of the "slow
    engine call" warnings at boot).
    """
    disabled_by_id = get_existing_disabled_batch(
        engine,
        [node_id for node_id, _declaration in declarations],
        label="MCPServer",
    )
    ingested = 0
    for node_id, declaration in declarations:
        engine.add_node(
            node_id,
            "MCPServer",
            {**declaration, "disabled": disabled_by_id.get(node_id, False)},
        )
        ingested += 1
    return ingested


def _ingest_mcp_config_capabilities(engine: Any) -> None:
    """Section 1 of :func:`_ingest_capabilities`: ``mcp_config.json`` -> ``MCPServer`` nodes."""
    try:
        mcp_servers = _load_mcp_config_servers()
        if mcp_servers is None:
            return
        declarations = _build_mcp_server_declarations(mcp_servers)
        ingested = _ingest_mcp_server_declarations(engine, declarations)
        logger.info("Ingested %d MCP capability declarations", ingested)
    except Exception as exc:
        logger.error("Failed to ingest MCP configuration: %s", exc)


def _discover_native_tool_entries(
    tools_package: Any,
) -> list[tuple[str, dict[str, Any]]]:
    """Import every non-package module under ``tools_package`` and collect its
    agentic-versioned functions as ``(node_id, properties)`` pairs.
    """
    import importlib
    import inspect
    import pkgutil

    from agent_utilities.security.persistence_privacy import sanitize_for_persistence

    prefix = tools_package.__name__ + "."
    tool_entries: list[tuple[str, dict[str, Any]]] = []
    for _importer, modname, ispkg in pkgutil.iter_modules(
        tools_package.__path__, prefix
    ):
        if ispkg:
            continue
        try:
            module = importlib.import_module(modname)
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if not hasattr(obj, "__agentic_version__"):
                    continue
                node_id = f"native_tool_{name}"
                description, _privacy = sanitize_for_persistence(
                    (obj.__doc__ or "")[:8192]
                )
                tool_entries.append(
                    (
                        node_id,
                        {
                            "name": name,
                            "description": str(description),
                            "version": obj.__agentic_version__,
                            "module": modname,
                        },
                    )
                )
        except Exception as exc:  # noqa: BLE001 — per-module best-effort skip; the outer scan already logs failures
            logger.debug(
                "Failed to ingest a native-tool module: %s", type(exc).__name__
            )
    return tool_entries


def _ingest_native_tool_capabilities(engine: Any) -> None:
    """Section 2 of :func:`_ingest_capabilities`: scan ``agent_utilities.tools`` -> ``NativeTool`` nodes."""
    try:
        import agent_utilities.tools

        tool_entries = _discover_native_tool_entries(agent_utilities.tools)
        # One batched round trip for every native tool's prior ``disabled``
        # flag instead of one query per tool.
        disabled_by_id = get_existing_disabled_batch(
            engine,
            [node_id for node_id, _properties in tool_entries],
            label="NativeTool",
        )
        for node_id, properties in tool_entries:
            engine.add_node(
                node_id,
                "NativeTool",
                {**properties, "disabled": disabled_by_id.get(node_id, False)},
            )
        logger.info("Ingested Native Tools")
    except Exception as exc:
        logger.error("Failed to scan native tools: %s", exc)


def _ingest_skill_provider_capabilities(
    engine: Any, skip_skill_names: frozenset[str]
) -> None:
    """Section 3 of :func:`_ingest_capabilities`: every skill provider's ``SKILL.md`` files."""
    try:
        from agent_utilities.core.config import config
        from agent_utilities.core.providers import resolve_skill_provider_dirs

        sources = resolve_skill_provider_dirs()
        if config.custom_skills_directory:
            sources.append(("configured-overlay", Path(config.custom_skills_directory)))
        ingested = sum(
            _ingest_skill_capabilities(
                engine,
                provider,
                root,
                skip_names=skip_skill_names,
            )
            for provider, root in sources
        )
        if ingested:
            logger.info("Ingested %d runnable skills", ingested)
    except Exception as e:
        logger.error("Failed to ingest skills: %s", e)


# ── Boot hydration plan (ingestion-hydration-program.md §3) ─────────────────
#
# ``_ingest_capabilities`` above (mcp_config.json / native tools / skills) is
# the ORIGINAL boot hydration; the two helpers below extend it with the
# capability legs Phases C and E built but never wired to a boot call. Each is
# its own best-effort, exception-isolated step — same shape as the ontology
# federation sync already nested inside :func:`_start_engine_bootstrap`'s
# background thread — so a failure in one never skips, or blocks serving for,
# the others. Fleet tool-schema ingestion (Phase A) and the mcp_config.json
# router (Phase B) need no boot call here: A rides its own hourly
# ``deploy/schedules.yml`` cadence (``fleet-tool-schema-sync``) and B rides the
# always-on codebase sweep's ``_route_classified_artifacts`` fan-out.


def _record_boot_hydration_step(
    engine: Any, name: str, priority: int, status: str
) -> None:
    """Persist the small boot plan state when the active engine accepts nodes.

    The record is deliberately stable per step, not a new unbounded node for
    every process start.  It gives operators a durable answer to "which boot
    hydration phase last ran?" while keeping every actual ingest on its owned
    incremental/checkpointed implementation.
    """
    add_node = getattr(engine, "add_node", None)
    if not callable(add_node):
        return
    try:
        add_node(
            f"boot-hydration:{name}",
            "HydrationPlanStep",
            {
                "name": name,
                "priority": priority,
                "status": status,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
    except Exception:  # noqa: BLE001 - observability must not stop hydration
        logger.debug("boot hydration plan record failed for %s", name, exc_info=True)


def _hydrate_code_and_configured_connectors(engine: Any) -> None:
    """Queue the lowest-priority checkpointed hydration work.

    Code uses the existing breadth ingest (which performs its git-SHA pre-skip)
    and connectors use ``sweep_all_sources`` (which prechecks the signed
    provider contract before queue publication).  Empty configured roots are a
    valid no-op for a packaged/tiny deployment; no guessed workstation path is
    ever scanned.
    """
    from agent_utilities.core.config import config
    from agent_utilities.core.workspace_config import workspace_project_roots
    from agent_utilities.knowledge_graph.assimilation.breadth_ingest import (
        run_breadth_ingest,
    )
    from agent_utilities.knowledge_graph.core.source_sync import sweep_all_sources

    library_roots = [p for p in config.kg_breadth_library_roots.split(",") if p]
    repo_roots = [p for p in config.kg_breadth_repo_roots.split(",") if p]
    if not library_roots and not repo_roots:
        repo_roots = workspace_project_roots()
    if library_roots or repo_roots:
        run_breadth_ingest(engine, library_roots=library_roots, repo_roots=repo_roots)
    # This is intentionally enqueue-only.  ``sweep_all_sources`` rejects known
    # unavailable providers before creating work, so boot never spends an engine
    # lease on a connector that is guaranteed to fail.
    sweep_all_sources(engine, mode="delta", enqueue=True, priority=3)


def _enqueue_fleet_tool_schema_hydration(engine: Any) -> None:
    """Queue the live 65+ server tool-schema probe as priority-one boot work.

    MCP declarations are cheap and synchronous; the live schemas are network
    work and belong on the durable connector lane.  A stable target lets the
    WorkItem queue deduplicate restarts in the same hour without its O(N)
    target scan. A later hour gets a fresh delta probe.

    ``task_type="capability_hydration"`` (CONCEPT:AU-ORCH.scheduling.acquisition-lane-fairness), NOT the
    generic ``connector_sync`` the */20m fleet sweep uses for every OTHER connector.
    Both ride the same ``connectors`` lane (same soft-timeout envelope), but a
    distinct type lets the worker pool reserve this job a claim floor
    (:func:`agent_utilities.knowledge_graph.core.engine_tasks.start_task_workers`)
    instead of it only ever landing on a worker the moment one of potentially
    dozens of concurrently-running legacy connector syncs happens to free up —
    the proven starvation mode where priority alone could not preempt
    already-running work.
    """
    submit = getattr(engine, "submit_task", None)
    if not callable(submit):
        return
    job_id = submit(
        target_path="fleet",
        is_codebase=False,
        provenance={"sync_mode": "delta", "boot_hydration": True},
        task_type="capability_hydration",
        priority=1,
        skip_dedupe=True,
        job_id=f"boot:fleet-tool-schemas:{datetime.now(UTC):%Y%m%d%H}",
    )
    logger.info("Queued fleet MCP tool-schema boot hydration: %s", job_id)


def _run_boot_hydration_plan(
    engine: Any, *, skip_skill_names: frozenset[str] = frozenset()
) -> None:
    """Run GraphOS boot hydration in its fixed resource-priority order.

    1. bounded GraphOS/fleet tool metadata, then runnable skills and MCP declarations;
    2. prompts/agent templates;
    3. package ontologies; and
    4. codebases and configured connectors through their durable delta queues.

    Each step is isolated so a failed optional source cannot prevent later
    priority classes from making progress.
    """
    steps = (
        ("fleet_tool_schemas", 1, lambda: _enqueue_fleet_tool_schema_hydration(engine)),
        ("graphos_tool_surface", 1, lambda: _ingest_self_tool_surface_at_boot(engine)),
        (
            "capabilities",
            1,
            lambda: _ingest_capabilities(engine, skip_skill_names=skip_skill_names),
        ),
        ("prompts", 2, _ingest_prompts_at_boot),
        ("ontologies", 3, lambda: _sync_ontologies_at_boot(engine)),
        (
            "code_and_connectors",
            4,
            lambda: _hydrate_code_and_configured_connectors(engine),
        ),
    )
    for name, priority, step in steps:
        _record_boot_hydration_step(engine, name, priority, "running")
        try:
            step()
        except Exception:  # noqa: BLE001 - each plan leg is independently retryable
            _record_boot_hydration_step(engine, name, priority, "failed")
            logger.error("Boot hydration step %s failed", name, exc_info=True)
        else:
            _record_boot_hydration_step(engine, name, priority, "completed")
    _record_hydration_manifest(engine)


def _record_hydration_manifest(engine: Any) -> None:
    """Build, sign and persist the boot hydration manifest.

    CONCEPT:AU-KG.audit.hydration-manifest-signed / CONCEPT:AU-KG.audit.hydration-absent-vs-hidden

    This is the one production call site of
    :mod:`agent_utilities.knowledge_graph.ingestion.hydration_manifest`. Without
    it the module was a 912-line orphan: its signing and its serving-vs-service
    two-authority cross-check were correct but ran on no live path, so the
    absent-vs-hidden ambiguity it exists to resolve stayed unresolved for every
    real deployment (and ``scripts/check_surface_parity.py`` flagged it as an
    unexposed capability). Running it HERE — immediately after every plan leg has
    reported completed/failed — is what makes the manifest a truthful record of
    what that boot actually hydrated, rather than a snapshot of an arbitrary
    later moment.

    Best-effort by design, exactly like :func:`_record_boot_hydration_step`:
    boot hydration must never be blocked by its own audit record. A missing
    release-signing key is the normal case for a dev checkout and degrades to a
    debug line rather than a failed boot.
    """
    from agent_utilities.knowledge_graph.ingestion.hydration_manifest import (
        build_hydration_manifest,
        persist_hydration_manifest,
        sign_hydration_manifest,
    )

    try:
        manifest = build_hydration_manifest()
        signed = sign_hydration_manifest(manifest)
        persist_hydration_manifest(engine, signed)
    except Exception as exc:  # noqa: BLE001 - the audit record must never block
        # boot hydration itself. The cause IS logged (exc.args[0], never
        # str()/repr() -- test_record_hydration_manifest_never_blocks_boot
        # asserts the real message reaches the log) so a persistently
        # unsignable/unbuildable manifest is diagnosable rather than silent.
        logger.debug(
            "boot hydration manifest not recorded: %s",
            exc.args[0] if exc.args else type(exc).__name__,
        )
    else:
        logger.info(
            "Recorded signed hydration manifest (generated_at=%s)",
            manifest.generated_at,
        )


def _ingest_prompts_at_boot() -> None:
    """Hydrate the ``:Prompt`` corpus at boot (Phase C → Phase F wiring).

    :func:`agent_utilities.agent.registry_builder.ingest_prompts_to_graph` is
    content-hash incremental (CONCEPT:EG-KG.storage.nonblocking-checkpoint,
    ``DeltaManifest`` category ``"prompt_base"``), so calling it on every boot
    is cheap: the first boot upserts every prompt, every later boot skips the
    unchanged ones. This runs inside the background bootstrap thread, which
    has no running event loop, so ``asyncio.run`` is the correct, safe way to
    drive the coroutine (mirrors the existing synchronous callers in
    ``registry_builder.py``/``package_install_ingest.py``) — never blocks
    graph-os startup or serving, and a failure here is isolated and logged,
    never raised into the caller.
    """
    try:
        from agent_utilities.agent.registry_builder import ingest_prompts_to_graph

        asyncio.run(ingest_prompts_to_graph())
        logger.info("Ingested prompt-base library at boot (Phase C hydration)")
    except Exception as exc:
        logger.error("Prompt-base boot ingestion failed: %s", exc)


def _graphos_self_tool_surface() -> list[dict[str, Any]]:
    """In-process snapshot of graph-os's own registered MCP tool surface.

    CONCEPT:AU-KG.ingest.self-tool-surface — the exact shape
    :func:`~agent_utilities.knowledge_graph.ingestion.engine.register_self_tool_surface_provider`
    expects: a plain, synchronous, zero-argument callable that reads the
    already-built ``REGISTERED_TOOLS`` dict (populated by
    ``register_tool_surface`` during :func:`_build_server`, which always runs
    before this process starts serving). Reading a module-global dict is the
    entire implementation — no network call, no MCP round-trip, no self-probe.
    """
    return [
        {
            "name": name,
            "description": (getattr(func, "__doc__", None) or "").strip(),
        }
        for name, func in sorted(REGISTERED_TOOLS.items())
    ]


def _ingest_self_tool_surface_at_boot(engine: Any) -> None:
    """Queue graph-os's own ~95 ``:MCPServer``/``:Tool`` nodes at boot.

    CONCEPT:AU-KG.ingest.self-tool-surface (Phase E → Phase F wiring). The
    provider is registered synchronously because workers share this process,
    while the native ChangeEnvelope materialization runs as a fenced,
    checkpointable priority-one WorkItem. Queue publication therefore cannot
    hold up prompts, ontologies, or later boot-plan checkpoints when the
    operational graph is cold or write-contended.
    """
    try:
        from agent_utilities.knowledge_graph.ingestion.engine import (
            register_self_tool_surface_provider,
        )

        register_self_tool_surface_provider(_graphos_self_tool_surface)
        submit = getattr(engine, "submit_task", None)
        if not callable(submit):
            return
        surface_digest = hashlib.sha256(
            json.dumps(
                _graphos_self_tool_surface(),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        job_id = submit(
            target_path="graph-os",
            is_codebase=False,
            provenance={"boot_hydration": True},
            task_type="self_tool_surface",
            priority=1,
            skip_dedupe=True,
            job_id=f"boot:self-tool-surface:{surface_digest}",
        )
        logger.info("Queued self tool-surface boot hydration: %s", job_id)
    except Exception as exc:
        logger.error("Self tool-surface boot enqueue failed: %s", exc)


def _sync_ontologies_at_boot(engine: Any) -> None:
    """Load package ontologies after runnable resources are available.

    CONCEPT:AU-KG.ontology.integrity-bootstrap — ``activate_graph()`` runs FIRST
    and unconditionally, so the dedicated ontology graph's SHACL/ICV integrity
    policy is registered even on a boot with zero federated ontology content
    to load (which would otherwise never reach the ``load()``/``_load_axioms``
    chokepoint that also performs activation). Idempotent; safe every boot.
    """
    sync_packages = engine._ontology_package_sync
    from agent_utilities.knowledge_graph.ontology.lifecycle import OntologyLifecycle

    lc = OntologyLifecycle(engine=engine)
    activation = lc.activate_graph()
    if not activation.get("activated") and activation.get("reason") not in (
        "no engine RDF surface",
    ):
        logger.error(
            "Ontology graph activation failed at boot: %s", activation.get("reason")
        )

    report = sync_packages(lc)
    if report.get("providers_loaded"):
        logger.info(
            "Ontology federation: loaded %d package ontolog(ies) at boot",
            report["providers_loaded"],
        )


def _set_readiness_authority(session: Any) -> object:
    """Hand the readiness probe the process's own verified authority."""

    from agent_utilities.observability.runtime_health import claim_readiness_authority

    return claim_readiness_authority(session)


def _release_readiness_authority(owner: object) -> None:
    """Release only the readiness authority claimed by this serving lifecycle."""

    from agent_utilities.observability.runtime_health import release_readiness_authority

    if not release_readiness_authority(owner):
        raise RuntimeError("graph-os no longer owns the readiness authority")


def _mint_process_session(transport: str) -> Any:
    """Mint the process's verified graph authority.

    Tiny packaged-local stdio uses an in-memory asymmetric authority. Every
    other topology resolves a token reference or performs OAuth2 client
    credentials, then validates the result through the same JWKS path as HTTP.
    The authority scopes background engine bootstrap for every transport and is
    additionally used for stdio tool calls, which have no request Authorization
    header.
    """
    from agent_utilities.core.config import config
    from agent_utilities.security.request_identity import (
        acquire_process_identity_token,
        local_process_authority_enabled,
        mint_actor_from_token_sync,
        mint_graph_session,
        mint_local_process_session,
    )

    if transport == "stdio" and local_process_authority_enabled(config):
        session = mint_local_process_session()
    else:
        token = acquire_process_identity_token(config)
        actor = mint_actor_from_token_sync(token)
        from agent_utilities.security.brain_context import CredentialLease

        expires_at = getattr(actor, "credential_expires_at", None)
        if expires_at is None:
            raise RuntimeError("Graph process identity has no bounded expiry")
        actor = replace(
            actor,
            credential_lease=CredentialLease(int(expires_at)),
        )
        session = mint_graph_session(actor)
    session.engine_verified_context()
    logger.info("Verified graph process authority minted")
    return session


def _same_process_authority(left: Any, right: Any) -> bool:
    """Return whether a renewed token preserves the original authority."""
    fields = (
        "actor_id",
        "actor_type",
        "roles",
        "tenant_id",
        "authenticated",
        "groups",
    )
    return all(
        getattr(left, name, None) == getattr(right, name, None) for name in fields
    )


def _renewed_process_actor(config: Any) -> Any:
    from agent_utilities.security.request_identity import (
        acquire_process_identity_token,
        local_process_authority_enabled,
        mint_actor_from_token_sync,
        mint_local_process_session,
    )

    if local_process_authority_enabled(config):
        return mint_local_process_session().actor
    token = acquire_process_identity_token(config)
    try:
        return mint_actor_from_token_sync(token)
    finally:
        del token


def _refresh_process_authority(session: Any) -> Any:
    """Renew one process lease without replacing captured sessions.

    External identities reacquire and validate their configured token. Tiny
    packaged-local stdio remints its in-memory asymmetric proof instead; it
    never falls through to an external-token lookup it cannot satisfy. After
    validation, only the bounded expiry is copied into the shared in-memory
    lease. Identity, roles, tenant, route, and policy may not change.
    """
    from agent_utilities.core.config import config
    from agent_utilities.knowledge_graph.core.session import SessionExpiredError

    lease = getattr(getattr(session, "actor", None), "credential_lease", None)
    if lease is None:
        raise RuntimeError("Graph process authority is not renewable")
    with _PROCESS_SESSION_REFRESH_LOCK:
        try:
            session.ensure_authority_current(minimum_ttl_seconds=30)
            return session
        except SessionExpiredError:  # noqa: BLE001 — expected: falls through to the renewal path below
            pass
        renewed_actor = _renewed_process_actor(config)
        if not _same_process_authority(session.actor, renewed_actor):
            raise RuntimeError("Graph process authority changed during renewal")
        expires_at = getattr(renewed_actor, "credential_expires_at", None)
        if expires_at is None or int(expires_at) <= int(time.time()) + 30:
            raise RuntimeError("Graph process authority renewal is too short-lived")
        lease.renew(int(expires_at))
        session.ensure_authority_current(minimum_ttl_seconds=30)
        return session


async def _ensure_process_authority_current() -> Any:
    """Ensure request/process authority without blocking the MCP event loop.

    D-SNV-5: an ambient session whose actor carries a renewable
    ``credential_lease`` (a server-minted process/client-credentials identity
    — never a caller-presented bearer JWT, which has no ``credential_lease``
    and stays exactly as fail-closed as before) is proactively renewed here
    the same way the stdio ``_PROCESS_SESSION`` fallback already was. This is
    the dispatch-entry check only; :func:`_keep_process_authority_current`
    covers the rest of a long-running dispatch.
    """
    from agent_utilities.knowledge_graph.core.session import (
        SessionExpiredError,
        current_session,
    )

    ambient = current_session()
    if ambient is not None:
        if getattr(getattr(ambient, "actor", None), "credential_lease", None) is None:
            # Not server-renewable: unchanged fail-closed behavior, no
            # minimum-TTL headroom requirement — this must never mask a
            # caller's real credential expiry.
            ambient.ensure_authority_current()
            return ambient
        try:
            ambient.ensure_authority_current(minimum_ttl_seconds=30)
        except SessionExpiredError:
            ambient = await asyncio.to_thread(_refresh_process_authority, ambient)
        if ambient is None:
            # `_refresh_process_authority` is typed `Any` (it renews in place and
            # returns the same session), so this should be unreachable in
            # practice — but if it ever did return nothing, failing closed with
            # the same PermissionError the no-ambient-session branch below uses
            # is correct, not an opaque AttributeError on the next line.
            raise PermissionError("Verified GraphSession required")
        ambient.ensure_authority_current(minimum_ttl_seconds=30)
        return ambient
    session = _PROCESS_SESSION
    if session is None:
        raise PermissionError("Verified GraphSession required")
    try:
        session.ensure_authority_current(minimum_ttl_seconds=30)
    except SessionExpiredError:
        session = await asyncio.to_thread(_refresh_process_authority, session)
    session.ensure_authority_current(minimum_ttl_seconds=30)
    return session


async def _keep_process_authority_current(session: Any) -> None:
    """Background keepalive for one in-flight dispatch under a renewable session.

    A tool dispatch may run for the whole ``_TOOL_CALL_TIMEOUT_S`` window
    (``_execute_tool``), but authority was previously checked only once, at
    entry (D-SNV-5: a real 192s ServiceNow delegation died mid-flight with
    ``SessionExpiredError`` because nothing renewed it after that). This loop
    renews the SAME mutable ``CredentialLease`` the dispatch's ambient
    ``GraphSession.actor`` already holds, so every downstream authority check
    (``GraphSession.ensure_authority_current`` at every engine boundary, e.g.
    ``graph_compute.py``'s ``_invoke_at``) sees it transparently — no session
    object is replaced and no verification is weakened; a caller-presented
    bearer JWT (no lease) never reaches this function at all.

    Runs only for the lifetime the caller's :func:`authority_keepalive_scope`
    is open (started and cancelled there) — never a free-running background
    task.
    """
    from agent_utilities.knowledge_graph.core.session import SessionExpiredError

    lease = session.actor.credential_lease
    try:
        while True:
            seconds_left = lease.expires_at - int(time.time())
            await asyncio.sleep(max(1.0, min(30.0, seconds_left - 30.0)))
            try:
                await asyncio.to_thread(_refresh_process_authority, session)
            except SessionExpiredError:
                # Fail-closed: the next real authority check (the deep engine
                # call already in flight, or the next one) raises for real.
                # D-SWG-3: log it here too so the keepalive giving up is visible
                # at the moment it happens, not only inferred later from a
                # downstream failure.
                logger.warning(
                    "Delegation authority keepalive stopping: session lease "
                    "already expired; the next authority check will fail closed"
                )
                return
            except Exception as exc:  # noqa: BLE001 — keepalive is best-effort renewal; a hard failure surfaces at the next real authority check either way
                logger.error(
                    "Delegation authority keepalive renewal failed (exception_type=%s)",
                    type(exc).__name__,
                )
    except asyncio.CancelledError:
        # D-SWG-3: expected, normal shutdown — the caller's
        # authority_keepalive_scope cancels this loop when the wrapped call
        # finishes. Logged at debug (routine, high-frequency) rather than
        # dropped silently.
        logger.debug("Delegation authority keepalive cancelled (scope exited)")


@contextlib.asynccontextmanager
async def authority_keepalive_scope(session: Any | None = None) -> AsyncIterator[None]:
    """Renew a renewable session's authority for the duration of the wrapped call.

    CONCEPT:AU-ORCH.execution.delegation-hot-path-authority — keep a long delegation authorized for its whole run, on every entrypoint, not just MCP dispatch.

    Every delegation entrypoint — the MCP ``_execute_tool`` dispatch, the
    ``agent-webui``/REST gateway, the messaging router (Telegram/Mattermost), the
    autonomous ``agent_dispatch_worker``, ``org_runtime``, a governed dynamic
    workflow, and the parallel engine — converges on the single function
    :meth:`agent_utilities.orchestration.manager.Orchestrator.execute_agent`.
    A background keepalive wired ONLY into ``_execute_tool`` (the original
    D-SNV-5 fix) therefore covered MCP-dispatched delegations only; anything that
    reached ``execute_agent`` some other way still expired mid-flight with
    ``SessionExpiredError``. This context manager is the one reusable primitive
    both ``_execute_tool`` and ``Orchestrator.execute_agent`` open, so every
    surface inherits the renewal by construction instead of each caller
    reimplementing it.

    Security posture is unchanged from the original ``_execute_tool``-only fix:

    * Only a server-minted, renewable ``credential_lease`` is ever proactively
      renewed. A caller-presented bearer JWT carries no ``credential_lease`` and
      is never touched here — it stays exactly as fail-closed as before.
    * The SAME mutable :class:`~agent_utilities.security.brain_context.CredentialLease`
      object the session already holds is renewed in place, so every downstream
      authority check (``GraphSession.ensure_authority_current``, e.g.
      ``graph_compute.py``'s ``_invoke_at``) sees it transparently — no session
      object is replaced and the expiry check itself is never weakened or
      lengthened.
    * ``session`` defaults to the ambient :func:`GraphSession
      <agent_utilities.knowledge_graph.core.session.current_session>` (falling
      back to the stdio ``_PROCESS_SESSION``, same as ``verified_tool_session_scope``)
      resolved WITHOUT raising when neither is set — this helper only ever adds
      renewal; it must never introduce a new precondition. A caller with no
      ambient/process session at all fails exactly as it already would, at the
      first real engine boundary (``SessionRequiredError``), not here.

    Idempotent/reentrant via :data:`_AUTHORITY_KEEPALIVE_ACTIVE`: a scope nested
    inside another already-open scope on the same task tree (a nested MCP
    dispatch that itself calls ``execute_agent``, or vice versa) is a no-op —
    the outer scope already renews the same lease, and the extra loop would only
    add redundant token round-trips, not extra safety.
    """
    if _AUTHORITY_KEEPALIVE_ACTIVE.get():
        yield
        return

    if session is None:
        from agent_utilities.knowledge_graph.core.session import current_session

        session = current_session() or _PROCESS_SESSION
    if session is None:
        yield
        return

    lease = getattr(getattr(session, "actor", None), "credential_lease", None)
    if lease is None:
        yield
        return

    guard_token = _AUTHORITY_KEEPALIVE_ACTIVE.set(True)
    keepalive = asyncio.ensure_future(_keep_process_authority_current(session))
    try:
        yield
    finally:
        keepalive.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive
        _AUTHORITY_KEEPALIVE_ACTIVE.reset(guard_token)


def _process_authority_refresh_loop(session: Any) -> None:
    """Keep a renewable process lease current for all captured worker sessions."""
    lease = getattr(getattr(session, "actor", None), "credential_lease", None)
    if lease is None:
        return
    while not _PROCESS_AUTHORITY_STOP.is_set():
        seconds_left = lease.expires_at - int(time.time())
        if seconds_left > 30:
            _PROCESS_AUTHORITY_STOP.wait(min(60.0, max(1.0, seconds_left - 30.0)))
            continue
        try:
            _refresh_process_authority(session)
        except Exception as exc:  # noqa: BLE001 - retry; expiry remains fail-closed
            logger.error(
                "Graph process authority renewal failed (exception_type=%s)",
                type(exc).__name__,
            )
            _PROCESS_AUTHORITY_STOP.wait(5.0)


def _start_process_authority_supervisor(session: Any) -> None:
    """Start the sole external-authority renewal supervisor when required."""
    global _PROCESS_AUTHORITY_THREAD
    _stop_process_authority_supervisor()
    _PROCESS_AUTHORITY_STOP.clear()
    if getattr(getattr(session, "actor", None), "credential_lease", None) is None:
        return
    thread = threading.Thread(
        target=_process_authority_refresh_loop,
        args=(session,),
        daemon=True,
        name="GraphProcessAuthority",
    )
    _PROCESS_AUTHORITY_THREAD = thread
    thread.start()


def _stop_process_authority_supervisor() -> None:
    """Stop and forget the process-authority supervisor."""
    global _PROCESS_AUTHORITY_THREAD
    _PROCESS_AUTHORITY_STOP.set()
    thread = _PROCESS_AUTHORITY_THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2.0)
    _PROCESS_AUTHORITY_THREAD = None


_BUNDLED_SKILL_READINESS: dict[str, Any] = {}
# A 9–10 GiB four-shard graph can legitimately need just over five minutes to
# rebuild its lazy-open indexes on slower storage. Keep this within the
# deployment's ten-minute startup probe while avoiding a pointless restart at
# the former five-minute boundary, which merely repeated the cold-open work.
_ENGINE_MATERIALIZATION_TIMEOUT_SECONDS = 540.0
# Materialization progresses in durable page batches measured in seconds on a
# large graph.  Four manifest reads per second added ~1,200 Python→native calls
# to a five-minute cold open without improving correctness.  One authoritative
# poll per second keeps readiness latency bounded while leaving the engine's
# foreground read lane available for the materializer and health probes.
_ENGINE_MATERIALIZATION_POLL_SECONDS = 1.0


def _set_bundled_skill_readiness(report: dict[str, Any]) -> None:
    """Publish packaged-skill readiness so /health can report it.

    Readiness no longer gates boot, so it MUST be observable at runtime —
    otherwise "serving degraded" is indistinguishable from "fully ready" to
    anything outside the process, which is the silent-failure pattern this
    codebase keeps getting bitten by.
    """
    _BUNDLED_SKILL_READINESS.clear()
    _BUNDLED_SKILL_READINESS.update(report)


def bundled_skill_readiness() -> dict[str, Any]:
    """The last packaged-skill readiness report (empty before bootstrap runs)."""
    return dict(_BUNDLED_SKILL_READINESS)


def _resolve_materialization_handles(engine: Any) -> tuple[Any, Any, str]:
    """Resolve the engine's native ``(query_cypher, list_graphs, graph_name)``.

    ``query_cypher``/``list_graphs`` come back ``None`` when the engine
    doesn't participate in native lifecycle (lightweight test engines and
    non-native backends have neither ``client`` nor ``graph_name``) or has
    no graph name at all — the caller treats either as "not_applicable".

    GraphOS owns the high-level IntelligenceGraphEngine; the lifecycle
    manifest belongs to its native GraphComputeEngine authority. Test tools
    and lower-level callers may pass that authority directly.
    """
    native_engine = getattr(engine, "graph_compute", None) or engine
    client = getattr(native_engine, "client", None)
    graph_name = str(getattr(native_engine, "graph_name", "") or "")
    query_cypher = getattr(native_engine, "query_cypher", None)
    tenants = getattr(client, "tenants", None)
    list_graphs = getattr(tenants, "list", None)
    if not graph_name or not callable(query_cypher) or not callable(list_graphs):
        return None, None, graph_name
    return query_cypher, list_graphs, graph_name


def _engine_read_probe_status(query_cypher: Any) -> str:
    """Run the one bounded read that both triggers and probes materialization.

    Returns ``"complete"`` (the read succeeded), ``"partial"`` (the read hit
    ``PARTIAL_MATERIALIZATION`` — the caller should keep polling the
    manifest), or ``"absent"`` (the graph does not exist). Any other
    exception propagates unchanged.
    """
    try:
        query_cypher("MATCH (n) RETURN n.id AS id LIMIT 1")
    except Exception as exc:
        # Control-flow only: this text never reaches a log or a caller, so it
        # is read via `exc.args` (never `str(exc)`/`repr(exc)`) to stay clear
        # of the served-boundary exception-surface policy on principle.
        detail = str(exc.args[0]) if exc.args else ""
        if "PARTIAL_MATERIALIZATION" in detail:
            return "partial"
        if "not found" in detail.lower():
            return "absent"
        raise
    return "complete"


def _resolve_manifest_entry(
    list_graphs: Any, graph_name: str, manifest_visible: bool | None
) -> tuple[dict[str, Any] | None, bool | None]:
    """Resolve this graph's manifest entry from ``list_graphs()``.

    Respects the "already known hidden" cache — once a poll has found the
    graph absent from the catalog (RLS-filtered), later polls skip
    re-querying the whole catalog and go straight to the read-probe
    fallback. Returns ``(entry, manifest_visible)``.
    """
    if manifest_visible is False:
        return None, manifest_visible
    entries = list_graphs() or []
    entry = next(
        (
            value
            for value in entries
            if (value.get("name") if isinstance(value, dict) else None) == graph_name
        ),
        None,
    )
    return entry, isinstance(entry, dict)


def _handle_materialized_manifest_entry(
    entry: dict[str, Any],
    graph_name: str,
    last_progress: tuple[str, int | None, int | None] | None,
) -> tuple[dict[str, Any] | None, tuple[str, int | None, int | None] | None]:
    """Interpret one polled, catalog-visible manifest entry.

    Returns ``(result, updated_last_progress)`` where ``result`` is the
    barrier's terminal return value once materialization is complete, or
    ``None`` to keep polling. Raises on a ``failed`` materialization phase.
    """
    phase = str(entry.get("materialization") or "unknown")
    valid = entry.get("valid") is True
    cursor = entry.get("completeness_cursor")
    node_offset = cursor.get("node_offset") if isinstance(cursor, dict) else None
    edge_offset = cursor.get("edge_offset") if isinstance(cursor, dict) else None
    progress = (phase, node_offset, edge_offset)
    if progress != last_progress:
        logger.info(
            "Epistemic graph materialization progress "
            "(graph=%s phase=%s node_offset=%s edge_offset=%s)",
            graph_name,
            phase,
            node_offset,
            edge_offset,
        )
        last_progress = progress
    if phase == "complete" and valid:
        logger.info(
            "Epistemic graph materialization ready "
            "(graph=%s node_offset=%s edge_offset=%s)",
            graph_name,
            node_offset,
            edge_offset,
        )
        return dict(entry), last_progress
    if phase == "failed":
        raise RuntimeError(
            "epistemic graph materialization failed "
            f"(graph={graph_name!r}, cursor={cursor!r})"
        )
    return None, last_progress


def _handle_hidden_manifest_entry(
    query_cypher: Any, graph_name: str
) -> dict[str, Any] | None:
    """Handle a poll where the graph's manifest entry is not catalog-visible.

    RLS may intentionally hide a protected graph (for example
    ``__secrets__``) from the catalog while still authorizing this
    process-scoped graph view. In that case the same bounded read that
    triggered materialization is the authoritative completion probe. Do not
    weaken catalog RLS merely to make boot observable. Returns a terminal
    result dict, or ``None`` to keep polling.
    """
    status = _engine_read_probe_status(query_cypher)
    if status == "absent":
        return {"graph": graph_name, "materialization": "absent"}
    if status == "complete":
        logger.info(
            "Epistemic graph materialization ready via authorized "
            "read probe (graph=%s; catalog manifest hidden)",
            graph_name,
        )
        return {
            "graph": graph_name,
            "materialization": "complete",
            "valid": True,
            "manifest_visible": False,
        }
    return None


def _initial_materialization_probe(
    engine: Any,
) -> tuple[dict[str, Any] | None, Any, Any, str]:
    """Resolve native handles, then run the initial bounded-read probe.

    Returns ``(early_result, query_cypher, list_graphs, graph_name)``.
    ``early_result`` is non-``None`` when the caller should return it
    immediately (``not_applicable`` / ``absent`` / ``complete``) rather than
    entering the manifest poll loop.
    """
    query_cypher, list_graphs, graph_name = _resolve_materialization_handles(engine)
    if query_cypher is None or list_graphs is None:
        early = {"graph": graph_name or None, "materialization": "not_applicable"}
        return early, query_cypher, list_graphs, graph_name

    status = _engine_read_probe_status(query_cypher)
    if status == "absent":
        return (
            {"graph": graph_name, "materialization": "absent"},
            query_cypher,
            list_graphs,
            graph_name,
        )
    if status == "complete":
        return (
            {"graph": graph_name, "materialization": "complete", "valid": True},
            query_cypher,
            list_graphs,
            graph_name,
        )
    return None, query_cypher, list_graphs, graph_name


def _poll_materialization_step(
    list_graphs: Any,
    query_cypher: Any,
    graph_name: str,
    last_progress: tuple[str, int | None, int | None] | None,
    manifest_visible: bool | None,
) -> tuple[
    dict[str, Any] | None,
    tuple[str, int | None, int | None] | None,
    bool | None,
]:
    """Run one manifest-poll iteration of :func:`_wait_for_engine_materialization`.

    Returns ``(result, last_progress, manifest_visible)``; ``result`` is the
    barrier's terminal return value once resolved, or ``None`` to keep
    polling.
    """
    entry, manifest_visible = _resolve_manifest_entry(
        list_graphs, graph_name, manifest_visible
    )
    if isinstance(entry, dict):
        result, last_progress = _handle_materialized_manifest_entry(
            entry, graph_name, last_progress
        )
        return result, last_progress, manifest_visible
    result = _handle_hidden_manifest_entry(query_cypher, graph_name)
    return result, last_progress, manifest_visible


def _wait_for_engine_materialization(
    engine: Any,
    *,
    timeout_seconds: float = _ENGINE_MATERIALIZATION_TIMEOUT_SECONDS,
    poll_seconds: float = _ENGINE_MATERIALIZATION_POLL_SECONDS,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Wait for a lazy-open graph to become complete before boot writes begin.

    The epistemic engine deliberately serves its catalog before a cold graph has
    finished paging into memory.  Reads and writes against that graph correctly
    fail with ``PARTIAL_MATERIALIZATION`` until its durable manifest is both
    ``complete`` and ``valid``.  GraphOS boot hydration and task workers are not
    ordinary retrying callers: starting them during that interval can discard
    their one-shot startup work.  This barrier starts lazy-open with one bounded
    read, then observes the authoritative ``ListGraphs`` manifest until it is
    safe to perform any boot mutation.

    Lightweight test engines and non-native backends have neither ``client`` nor
    ``graph_name`` and therefore do not participate in this native lifecycle.
    A graph absent from a new empty engine is likewise left for normal creation.
    """
    early_result, query_cypher, list_graphs, graph_name = (
        _initial_materialization_probe(engine)
    )
    if early_result is not None:
        return early_result

    logger.info(
        "GraphOS waiting for epistemic graph materialization before hydration "
        "(graph=%s timeout_seconds=%g)",
        graph_name,
        timeout_seconds,
    )
    shutdown = stop_event or _PROCESS_AUTHORITY_STOP
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_progress: tuple[str, int | None, int | None] | None = None
    manifest_visible: bool | None = None
    while True:
        result, last_progress, manifest_visible = _poll_materialization_step(
            list_graphs, query_cypher, graph_name, last_progress, manifest_visible
        )
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "epistemic graph did not become completely materialized before "
                f"GraphOS boot hydration (graph={graph_name!r}, "
                f"timeout_seconds={timeout_seconds:g}, last={last_progress!r})"
            )
        if shutdown.wait(max(0.0, poll_seconds)):
            raise InterruptedError(
                "GraphOS shutdown cancelled the graph materialization barrier"
            )


def _engine_bootstrap_is_client(engine: Any, fallback_role: str) -> bool:
    """Return whether either the requested or elected engine role is client."""
    requested_role = (
        (getattr(engine, "_daemon_role", None) or fallback_role).strip().lower()
    )
    effective_role = (
        (getattr(engine, "_effective_role", None) or requested_role).strip().lower()
    )
    return "client" in {requested_role, effective_role}


def _run_enabled_boot_hydration(
    engine: Any,
    *,
    client_role: bool,
    background_sync_enabled: bool,
    skip_skill_names: frozenset[str],
) -> None:
    """Hydrate only on the elected background-sync host."""
    if client_role or not background_sync_enabled:
        return
    _run_boot_hydration_plan(engine, skip_skill_names=skip_skill_names)


def _start_engine_bootstrap(session: Any) -> None:
    """Establish engine/skill readiness, then start noncritical services."""
    from agent_utilities.core.config import config
    from agent_utilities.knowledge_graph.core.engine_tasks import (
        _authorized_background_thread,
        _require_verified_background_session,
        daemon_role,
    )
    from agent_utilities.knowledge_graph.core.session import use_session
    from agent_utilities.security.brain_context import use_actor
    from agent_utilities.skills import BUNDLED_SKILLS

    verified_session = _require_verified_background_session(session)
    with (
        use_actor(verified_session.actor),
        use_session(verified_session),
    ):
        engine = _get_engine()
        # Correctness gate: unlike missing packaged skills, a partially
        # materialized graph cannot safely accept one-shot boot hydration or
        # worker claims.  Let this failure stop startup so the orchestrator can
        # retry the process instead of advertising a silently incomplete graph.
        _wait_for_engine_materialization(engine)
    try:
        with (
            use_actor(verified_session.actor),
            use_session(verified_session),
        ):
            readiness = _ensure_bundled_skills_ready(engine)
    except Exception as exc:
        # Packaged-skill readiness is a CAPABILITY concern, not a correctness or
        # security one, so it must not decide whether graph-os serves at all. A
        # server that refuses to boot because some bundled skills did not ingest
        # takes down every unrelated tool, the health surface, and the operator's
        # ability to diagnose the very problem — the failure mode is far worse
        # than running degraded. Record it, surface it in /health, keep serving.
        # The LOG line preserves the real cause (an operator needs to see WHICH
        # packaged skill failed and why, not just "SERVING DEGRADED" for every
        # distinct cause — HANDOFF-2026-07-22 turned exactly this omission into
        # an hours-long dead end); ``exc.args[0]`` (not ``str(exc)``/``exc``
        # itself, and no ``exc_info=True``) keeps the served-boundary
        # exception-surface gate satisfied. The `/health`-published readiness
        # dict below is a DIFFERENT, wider-audience surface and stays
        # type-only (D-LR-2).
        logger.error(
            "GraphOS packaged-skill bootstrap failed; SERVING DEGRADED (%s: %s)",
            type(exc).__name__,
            exc.args[0] if exc.args else "",
        )
        _set_bundled_skill_readiness(
            {
                "required": len(BUNDLED_SKILLS),
                "ready": 0,
                "not_ready": sorted(BUNDLED_SKILLS),
                # See _ensure_bundled_skills_ready's identical comment: this
                # dict is published for the /health HTTP surface, not logged,
                # so only the exception TYPE is exposed here (D-LR-2).
                "error": type(exc).__name__,
            }
        )
        return

    _set_bundled_skill_readiness(readiness)
    if readiness.get("not_ready"):
        logger.error(
            "GraphOS is SERVING DEGRADED: %d/%d packaged skills ready, not_ready=%s",
            readiness.get("ready", 0),
            readiness.get("required", 0),
            readiness.get("not_ready"),
        )
    logger.info(
        "GraphOS packaged-skill readiness established (%d/%d)",
        readiness["ready"],
        readiness["required"],
    )
    # An explicit client role is a hard serving-plane boundary. In particular,
    # stale KG_LOOP/maintenance settings must not turn a network-facing GraphOS
    # process into an autonomous scheduler or queue worker. The requested role
    # captured on the engine wins over any later process-environment mutation.
    client_role = _engine_bootstrap_is_client(engine, daemon_role())
    if not client_role:
        # BUG-295 (NE-009/NE-020): the daemon role is the one that runs the
        # unified scheduler (start_daemons() below), whose every tick reads
        # and writes the isolated `__control__` graph under THIS process's
        # own verified identity. Admit that identity into the narrow
        # control:system RBAC role idempotently, once per process, before
        # the scheduler can fire a single tick against it. Auto-at-boot by
        # design (see system_rbac_admission's module docstring, "Auto-
        # admission at boot"); degrades honestly — never crashes the
        # process and never claims success it did not confirm. Until an
        # operator seeds the missing NE-021 provisioner credential (or the
        # engine is unreachable), this logs the exact actionable cause once
        # per 30s backoff window and the scheduler keeps failing exactly as
        # visibly as it does today, but now with a diagnosis attached.
        try:
            from agent_utilities.security.system_rbac_admission import (
                ensure_system_principal_access,
            )

            ensure_system_principal_access(verified_session.actor.actor_id)
        except Exception as exc:  # noqa: BLE001 - must never block/crash boot
            logger.error(
                "system-principal control-graph admission not confirmed; "
                "the scheduler will keep failing until this is resolved "
                "(%s: %s)",
                type(exc).__name__,
                exc,
            )
    start_daemons = getattr(engine, "start_background_daemons", None)
    if not client_role and callable(start_daemons):
        start_daemons()

    def _bootstrap_engine() -> None:
        try:
            if (
                not client_role
                and engine
                and engine.backend
                and not getattr(engine.backend, "read_only", False)
            ):
                engine.start_task_workers()
            # The listener barrier already reconciled bundled skills.  Continue
            # broader discovery via the durable, fixed-priority plan without
            # blocking serving.
            _run_enabled_boot_hydration(
                engine,
                client_role=client_role,
                background_sync_enabled=config.knowledge_graph_sync_background,
                skip_skill_names=frozenset(BUNDLED_SKILLS),
            )
        except Exception as exc:
            logger.error("KG engine background bootstrap failed: %s", exc)

    try:
        _authorized_background_thread(
            verified_session,
            _bootstrap_engine,
            name="KGEngineBootstrap",
        ).start()
    except Exception as exc:
        # Packaged delegation is already ready. Optional workers, provider
        # discovery, and ontology federation remain retryable operational work.
        logger.error("GraphOS noncritical bootstrap launch failed: %s", exc)


def set_process_session(session: Any) -> None:
    """Keep bootstrap authority state synchronized with the host runtime."""

    global _PROCESS_SESSION
    _PROCESS_SESSION = session


BOOTSTRAP_EXPORTS = tuple(
    name
    for name, value in globals().items()
    if getattr(value, "__module__", None) == __name__ or name.startswith("DEFAULT_")
)
