"""Native swarm supervisory plane (CONCEPT:AU-OS.safety.ontological-guardrail).

CONCEPT:AU-OS.state.fleet-supervisory-plane-at — Fleet supervisory plane at scale — SQL
aggregation, paginated and filtered session queries, and desired-state pause and kill
reconciliation across hosts

A single pane of glass over the running fleet, exposed through the API gateway —
no separate supervisor service. Everything here surfaces state the ecosystem
*already* maintains:

* **topology / health** — the durable session registry and goal registry in
  :mod:`agent_utilities.core.sessions` (per-host SQLite by default, the shared
  Postgres state store when ``state_db_uri`` is set —
  CONCEPT:AU-OS.state.unified-durable-state-externalization).
  Aggregations run in SQL (``COUNT``/``GROUP BY``) and listings are paginated
  + status-filterable, so the handlers stay O(page), not O(fleet)
  (CONCEPT:AU-OS.state.fleet-supervisory-plane-at).
* **pause / kill** — emergency containment as *desired-state writes*: targets
  local to this gateway are cancelled in-process (fast path) and finalized;
  sessions owned by another host get ``pause_requested``/``kill_requested``,
  which the owning host's goal loop reconciles on its next tick
  (CONCEPT:AU-OS.state.fleet-supervisory-plane-at).
* **approvals** — pending mutation/risk approvals are ``action.approval`` EG
  ControlLease records (a generic ``ActionApproval`` node write is refused by
  the connected engine's native row guard; eg-workitem WRAPUP §3d), read and
  decided through the parity-covered ``graph_query`` / ``graph_governance``
  tools.
* **trace / touched** — swarm-wide correlation reads over typed EG surfaces:
  ``ListWorkItems.metadata_match`` for ``fleet_trace``'s correlation-id join,
  and a bounded ``StreamRead`` over the append-only ``fleet.events`` stream
  for ``fleet_touched``'s blast-radius query (eg-workitem WRAPUP EG-5).

These handlers are plain Starlette callables mounted by the gateway; the
``agent-webui`` Fleet Dashboard consumes them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import msgpack
from agent_utilities.core import sessions as _sessions
from agent_utilities.orchestration.action_policy import (
    ACTION_APPROVAL_KIND,
    approval_lease_to_props,
)
from agent_utilities.orchestration.fleet_health import (
    FleetDependencyEvidence,
    FleetHealthSnapshot,
    _domain_sql,
    collect_fleet_health,
    health_payload,
    mark_dependency_failure,
)
from agent_utilities.security.error_surface import public_error_payload
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_MAX_PAGE = 1000
#: The verified session scope that grants whole-fleet supervision.
_GRAPH_ADMIN_SCOPE = "kg:admin"


def _tenant_sql(dialect: str) -> str:
    """SQL expression extracting a session's tenant from its metadata."""
    if dialect == "postgres":
        return (
            "CASE WHEN pg_input_is_valid(metadata_json, 'jsonb') "
            "THEN NULLIF(metadata_json::jsonb ->> 'tenant', '') ELSE NULL END"
        )
    return (
        "CASE WHEN json_valid(metadata_json) "
        "THEN NULLIF(json_extract(metadata_json, '$.tenant'), '') ELSE NULL END"
    )


def _tenant_scope(dialect: str) -> tuple[str, list[Any]]:
    """Return an ``(sql_predicate, params)`` restricting rows to the caller's org.

    The tenant-scoped supervisory plane (CONCEPT:AU-OS.safety.ontological-guardrail +
    OS-5.14): an org's
    caller sees only its own sessions/agents; a verified platform admin sees
    the whole fleet. Missing or tenantless identity fails closed.
    """
    try:
        from agent_utilities.api.session import resolve_session

        session = resolve_session()
        actor = session.actor
        if not actor.authenticated:
            raise PermissionError(
                "Fleet supervision requires verified tenant authority"
            )
        if _GRAPH_ADMIN_SCOPE in session.scopes:
            return "", []
        tenant = str(session.tenant or "").strip()
        if not tenant:
            raise PermissionError(
                "Fleet supervision requires verified tenant authority"
            )
        return f"{_tenant_sql(dialect)} = ?", [tenant]
    except PermissionError:
        raise
    except Exception as exc:
        raise PermissionError("Fleet supervision authority is unavailable") from exc


def _caller_tenant_scope(
    caller: Any, *, required_scope: str = "fleet:read"
) -> Callable[[str], tuple[str, list[Any]]]:
    """Bind a supervisory read to operation-pipeline verified caller facts."""
    if not getattr(caller, "authenticated", False) or required_scope not in getattr(
        caller, "effective_scopes", frozenset()
    ):
        raise PermissionError("Fleet supervision requires verified read authority")
    tenant = str(getattr(caller, "tenant", "") or "").strip()
    if not tenant:
        raise PermissionError("Fleet supervision requires verified tenant authority")

    def scope(dialect: str) -> tuple[str, list[Any]]:
        return f"{_tenant_sql(dialect)} = ?", [tenant]

    return scope


def fleet_health_for_caller(caller: Any) -> dict[str, Any]:
    """Read the existing health contract under an authenticated op caller."""
    snapshot = collect_fleet_health(scope_resolver=_caller_tenant_scope(caller))
    return health_payload(_tenant_health_projection(snapshot))


def fleet_topology_for_caller(
    caller: Any, *, limit: int = 200, offset: int = 0, status: str | None = None
) -> dict[str, Any]:
    """Read one bounded topology page under the same tenant as health."""
    scope = _caller_tenant_scope(caller)
    snapshot = collect_fleet_health(scope_resolver=scope)
    snapshot, rows = _topology_rows(
        snapshot, status, limit, offset, scope, allow_control_only=True
    )
    return _topology_payload(
        _tenant_health_projection(snapshot), rows, limit, offset, caller_scoped=True
    )


def _tenant_health_projection(snapshot: FleetHealthSnapshot) -> FleetHealthSnapshot:
    """Keep tenant SQL evidence; suppress process-global goals and workers.

    AU's collector accepts a tenant predicate for its control-store SQL only.
    Its goal and dispatch registries are process-wide and carry no tenant
    filter. Their counts, rows, and healthy evidence cannot describe this
    caller's tenant until those sources gain tenant-scoped authority.
    """
    if not isinstance(snapshot, FleetHealthSnapshot):
        raise RuntimeError("fleet health source contract is unavailable")
    checked_at = snapshot.evidence.generated_at
    dependencies = dict(snapshot.evidence.dependencies)
    control = dependencies.get("control_store")
    diagnostics = [
        f"control_store: {message}"
        for message in (control.diagnostics if control is not None else ())
    ]
    for name in ("goal_rehydration", "worker_registry"):
        dependencies[name] = FleetDependencyEvidence(
            status="unavailable",
            checked_at=checked_at,
            diagnostics=[f"{name}: tenant-scoped source unavailable"],
        )
        diagnostics.append(f"{name}: tenant-scoped source unavailable")
    status = "partial" if control and control.status != "unavailable" else "unavailable"
    last_success_at = control.last_success_at if control is not None else None
    evidence = snapshot.evidence.model_copy(
        update={
            "status": status,
            "ready": False,
            "autoscaling_ready": False,
            "convergence_ready": False,
            "last_success_at": last_success_at,
            "freshness_seconds": (
                max(0.0, checked_at - last_success_at)
                if last_success_at is not None
                else None
            ),
            "dependencies": dependencies,
            "diagnostics": diagnostics[:4],
        }
    )
    return replace(snapshot, evidence=evidence, goals=None, dispatch_workers=None)


def fleet_set_status_for_caller(
    caller: Any,
    *,
    action: str,
    session_ids: tuple[str, ...] = (),
    domain: str | None = None,
) -> dict[str, Any]:
    """Contain only sessions proven to belong to the verified caller tenant."""
    if action not in {"pause", "kill"}:
        raise ValueError("unknown fleet containment action")
    if getattr(caller, "principal_kind", None) != "human" or getattr(
        caller, "delegated", True
    ):
        raise PermissionError("fleet containment requires a direct human caller")
    scope = _caller_tenant_scope(caller, required_scope="fleet:control")
    if bool(session_ids) == bool(domain):
        raise ValueError("provide session_ids or domain")
    if session_ids:
        if len(session_ids) > _MAX_PAGE or len(set(session_ids)) != len(session_ids):
            raise ValueError("invalid session target list")
        found = _fetch_sessions_by_ids(session_ids, scope)
        if set(found) != set(session_ids):
            raise PermissionError("fleet target is unavailable")
        target_ids = list(session_ids)
    else:
        rows = _fetch_sessions(domain=domain, limit=_MAX_PAGE + 1, scope_resolver=scope)
        if not rows or len(rows) > _MAX_PAGE:
            raise ValueError("fleet domain target is unavailable or too large")
        target_ids = [str(row["id"]) for row in rows]
    final_status = "paused" if action == "pause" else "cancelled"
    requested_status = "pause_requested" if action == "pause" else "kill_requested"
    affected, applied = _write_fleet_status(
        target_ids,
        final_status,
        requested_status,
        _multi_host_state(),
        scope_resolver=scope,
    )
    return {
        "status": "success",
        "action": final_status,
        "affected": affected,
        "applied": applied,
        "count": len(affected),
    }


def _fetch_sessions_by_ids(
    session_ids: tuple[str, ...], scope_resolver: Callable[[str], tuple[str, list[Any]]]
) -> list[str]:
    conn = _sessions._connect_db()
    try:
        scope_sql, scope_params = scope_resolver(conn.dialect)
        placeholders = ", ".join("?" for _ in session_ids)
        cur = conn.cursor()
        cur.execute(
            f"SELECT id FROM sessions WHERE id IN ({placeholders}) AND {scope_sql}",
            [*session_ids, *scope_params],
        )
        return [str(row["id"]) for row in cur.fetchall()]
    finally:
        conn.close()


async def _verified_graph_client(required_scope: str) -> tuple[Any, Any, Any]:
    """Resolve ``(session, session-routed EG client, verified claims)``.

    Mirrors ``gateway/graph_api.py``'s SPARQL/SQL handlers: every typed EG
    call this module makes runs under ``client.use_verified_context(claims)``
    for the caller's own verified tenant, never a raw unauthenticated client.
    """
    from agent_utilities.api.session import resolve_session

    from graph_os.gateway.ports import gateway_application

    session = resolve_session(required_scope=required_scope)
    claims = session.engine_verified_context()
    client = gateway_application().graph_client(session.tenant)
    return session, client, claims


def _page_params(
    request: Request, default_limit: int = 200
) -> tuple[int, int, str | None]:
    """Parse ``limit``/``offset``/``status`` query params with sane bounds."""
    params = getattr(request, "query_params", {}) or {}
    try:
        limit = max(1, min(int(params.get("limit", default_limit)), _MAX_PAGE))
    except (TypeError, ValueError):
        limit = default_limit
    try:
        offset = max(0, int(params.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    status = params.get("status") or None
    return limit, offset, status


def _fetch_sessions(
    status: str | None = None,
    domain: str | None = None,
    limit: int = 200,
    offset: int = 0,
    scope_resolver: Callable[[str], tuple[str, list[Any]]] = _tenant_scope,
) -> list[dict[str, Any]]:
    """Read a filtered, paginated page of sessions tagged with their domain."""
    conn = _sessions._connect_db()
    try:
        dom = _domain_sql(conn.dialect)
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if domain:
            where.append(f"{dom} = ?")
            params.append(domain)
        scope_sql, scope_params = scope_resolver(conn.dialect)
        if scope_sql:
            where.append(scope_sql)
            params.extend(scope_params)
        sql = " ".join(("SELECT *,", dom, "AS domain FROM sessions"))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _multi_host_state() -> bool:
    """True when sessions may be owned by other hosts (state externalized)."""
    from agent_utilities.core.state_store import postgres_state_enabled

    return postgres_state_enabled()


async def fleet_health(request: Request) -> JSONResponse:
    """Aggregate swarm health from the shared fail-closed evidence collector."""

    snapshot = collect_fleet_health(scope_resolver=_tenant_scope)
    return JSONResponse(
        health_payload(snapshot),
        status_code=200 if snapshot.evidence.ready else 503,
        headers={"Cache-Control": "no-store"},
    )


async def fleet_topology(request: Request) -> JSONResponse:
    """Live agent/team topology grouped by enterprise domain (paginated)."""
    snapshot = collect_fleet_health(scope_resolver=_tenant_scope)
    limit, offset, status = _page_params(request)
    snapshot, rows = _topology_rows(snapshot, status, limit, offset)
    payload = _topology_payload(snapshot, rows, limit, offset)
    return JSONResponse(
        payload,
        status_code=200 if snapshot.evidence.ready else 503,
        headers={"Cache-Control": "no-store"},
    )


def _topology_payload(
    snapshot: Any,
    rows: list[dict[str, Any]],
    limit: int,
    offset: int,
    *,
    caller_scoped: bool = False,
) -> dict[str, Any]:
    domains = _group_sessions(rows)
    control = snapshot.evidence.dependencies.get("control_store")
    page_ready = snapshot.evidence.convergence_ready or (
        caller_scoped
        and control is not None
        and control.status in {"healthy", "degraded"}
    )

    # Totals come from the same SQL aggregate evidence, never from the returned
    # page. ``None`` is intentional when the authoritative read was not safe.
    total_sessions = (
        snapshot.sessions.get("total") if snapshot.sessions is not None else None
    )
    active_goals = {} if caller_scoped else getattr(_sessions, "active_goals", {})
    goals = (
        _sessions.make_serializable(list(active_goals.values()))
        if not caller_scoped
        and snapshot.goals is not None
        and hasattr(_sessions, "make_serializable")
        else None
    )
    workers = snapshot.dispatch_workers
    payload = {
        "generated_at": snapshot.evidence.generated_at,
        "domains": list(domains.values()) if page_ready else None,
        "goals": goals,
        "dispatch_workers": workers,
        "totals": {
            "domains": len(domains) if page_ready else None,
            "sessions": total_sessions,
            "dispatch_workers": len(workers) if workers is not None else None,
        },
        "page": {
            "limit": limit,
            "offset": offset,
            "returned": len(rows) if page_ready else None,
        },
        "evidence": snapshot.evidence.model_dump(mode="json"),
    }
    return payload


def _topology_rows(
    snapshot: Any,
    status: str | None,
    limit: int,
    offset: int,
    scope_resolver: Callable[[str], tuple[str, list[Any]]] = _tenant_scope,
    *,
    allow_control_only: bool = False,
) -> tuple[Any, list[dict[str, Any]]]:
    control = snapshot.evidence.dependencies.get("control_store")
    page_ready = snapshot.evidence.convergence_ready or (
        allow_control_only
        and control is not None
        and control.status in {"healthy", "degraded"}
    )
    if not page_ready:
        return snapshot, []
    try:
        return snapshot, _fetch_sessions(
            status=status,
            limit=limit,
            offset=offset,
            scope_resolver=scope_resolver,
        )
    except PermissionError:
        raise
    except Exception as exc:
        failed = mark_dependency_failure(
            snapshot, "control_store", "control_store.topology_page", exc
        )
        return failed, []


def _group_sessions(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    domains: dict[str, dict[str, Any]] = {}
    for session in rows:
        domain = session["domain"]
        bucket = domains.setdefault(domain, {"domain": domain, "sessions": []})
        bucket["sessions"].append(
            {
                "id": session.get("id"),
                "status": session.get("status"),
                "background": bool(session.get("background", 0)),
                "needs_input": bool(session.get("needs_input", 0)),
                "updated_at": session.get("updated_at"),
            }
        )
    return domains


def _cancel_session_tasks(session_id: str) -> bool:
    """Cancel any in-flight goal-loop task bound to ``session_id`` (in-memory)."""
    cancelled = False
    runs = getattr(_sessions, "background_goal_runs", {})
    for goal_id, run in list(runs.items()):
        if run.get("session_id") == session_id:
            task = run.get("task")
            if task is not None and not task.done():
                task.cancel()
            runs.pop(goal_id, None)
            active = getattr(_sessions, "active_goals", {})
            if goal_id in active:
                goal_status: Any = getattr(
                    _sessions, "GoalStatus", type("G", (), {"CANCELLED": "cancelled"})
                )
                active[goal_id]["status"] = goal_status.CANCELLED
                persist = getattr(_sessions, "_persist_goal", None)
                if callable(persist):
                    persist(goal_id)
            cancelled = True
    return cancelled


async def _set_fleet_status(request: Request, new_status: str) -> JSONResponse:
    """Shared pause/kill: desired-state writes + local fast-path cancel (OS-5.18).

    Body accepts either ``{"session_ids": [...]}`` or ``{"domain": "finance"}``
    (whole-domain containment). Sessions whose goal loop runs in THIS process
    are cancelled immediately and set to the final status. With durable state
    externalized (``state_db_uri``), sessions owned by other hosts instead get
    ``pause_requested``/``kill_requested``, which the owning host's session
    loop reconciles (see ``core.sessions._desired_session_action``). Under the
    single-host SQLite default every session is local, so the final status is
    applied directly — unchanged behavior.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    target_ids = _target_session_ids(body)

    if not target_ids:
        return JSONResponse(
            {
                "status": "error",
                "message": "Provide session_ids or a domain to target.",
            },
            status_code=400,
        )

    multi_host = _multi_host_state()
    requested_status = "pause_requested" if new_status == "paused" else "kill_requested"

    affected, applied = _write_fleet_status(
        target_ids, new_status, requested_status, multi_host
    )

    return JSONResponse(
        {
            "status": "success",
            "action": new_status,
            "affected": affected,
            "applied": applied,
            "count": len(affected),
        }
    )


def _target_session_ids(body: dict[str, Any]) -> list[str]:
    target_ids = list(body.get("session_ids") or [])
    domain = body.get("domain")
    if domain and not target_ids:
        return [
            session["id"]
            for session in _fetch_sessions(domain=domain, limit=_MAX_PAGE)
            if session.get("id")
        ]
    return target_ids


def _write_fleet_status(
    target_ids: list[str],
    new_status: str,
    requested_status: str,
    multi_host: bool,
    scope_resolver: Callable[[str], tuple[str, list[Any]]] = _tenant_scope,
) -> tuple[list[str], dict[str, str]]:
    applied: dict[str, str] = {}
    conn = _sessions._connect_db()
    try:
        cur = conn.cursor()
        scope_sql, scope_params = scope_resolver(conn.dialect)
        scoped_update = (
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?"
            + (f" AND {scope_sql}" if scope_sql else "")
        )
        for session_id in target_ids:
            status = requested_status if multi_host else new_status
            cur.execute(
                scoped_update,
                [status, time.time(), session_id, *scope_params],
            )
            if cur.rowcount != 1:
                raise PermissionError("fleet target is unavailable")
            applied[session_id] = status
        # Persist every scoped row before canceling any in-process task. A lost
        # tenant fence cannot leave one task canceled after a rolled-back batch.
        conn.commit()
        for session_id in target_ids:
            local = _cancel_session_tasks(session_id)
            if local and multi_host:
                cur.execute(
                    scoped_update,
                    [new_status, time.time(), session_id, *scope_params],
                )
                if cur.rowcount != 1:
                    raise PermissionError("fleet target is unavailable")
                applied[session_id] = new_status
        conn.commit()
    finally:
        conn.close()
    return list(applied), applied


async def fleet_pause(request: Request) -> JSONResponse:
    """Pause sessions (by ids or whole domain) — sets status='paused', halts loops."""
    return await _set_fleet_status(request, "paused")


async def fleet_kill(request: Request) -> JSONResponse:
    """Cancel sessions by id or domain for blast-radius containment."""
    return await _set_fleet_status(request, "cancelled")


async def fleet_approvals(request: Request) -> JSONResponse:
    """List pending mutation/risk approvals.

    Pending approvals are ``active`` ``action.approval`` EG ControlLease
    records filed by the operational ActionPolicy gate
    (CONCEPT:AU-OS.deployment.fleet-lifecycle-control). A WorkItem is created
    or released only after authorization; unclaimed operational work is never
    misrepresented as a pending human decision.
    """

    pending: list = []
    note = None
    try:
        session, client, claims = await _verified_graph_client("kg:read")
        with client.use_verified_context(claims):
            page = await client.control_leases.list(
                tenant=session.tenant,
                kind=ACTION_APPROVAL_KIND,
                status="active",
                limit=200,
            )
        for lease in (page or {}).get("leases") or []:
            if isinstance(lease, dict):
                pending.append(approval_lease_to_props(lease))
    except Exception as exc:
        logger.warning("Fleet approval source unavailable: %s", exc)
        note = "approval_source_unavailable"
    payload = {"status": "success", "pending": pending}
    if note:
        payload["note"] = note
    return JSONResponse(payload)


async def fleet_grant_approval(request: Request) -> JSONResponse:
    """Atomically grant or deny a pending ``action.approval`` lease by id."""

    try:
        body = await request.json()
    except Exception:
        body = {}
    job_id = body.get("job_id")
    decision = body.get("decision", "approved")
    if not job_id:
        return JSONResponse(
            {"status": "error", "message": "job_id is required"}, status_code=400
        )
    if str(job_id).startswith("action_approval:"):
        try:
            from agent_utilities.orchestration.approval import (
                ApprovalSurface,
                decide_action_approval,
            )

            from graph_os.gateway.ports import gateway_application

            engine = gateway_application().engine()
            result = decide_action_approval(
                engine, str(job_id), str(decision), ApprovalSurface.OPERATOR_CONSOLE
            )
            return JSONResponse(
                {
                    "status": "success",
                    "result": result,
                }
            )
        except LookupError as exc:
            return JSONResponse(
                {"status": "error", "message": type(exc).__name__}, status_code=409
            )
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "message": type(exc).__name__}, status_code=400
            )
        except Exception as exc:
            return JSONResponse(
                public_error_payload(exc, logger=logger), status_code=500
            )
    return JSONResponse(
        {
            "status": "error",
            "message": "approval_id must identify an ActionApproval",
        },
        status_code=400,
    )


async def fleet_verify_action(request: Request) -> JSONResponse:
    """Pre-execution assurance check for a proposed ActionPolicy payload.

    CONCEPT:AU-OS.governance.assurance-state-machine-verifier — the REST twin of the
    ``graph_governance action=verify_action`` MCP tool; both dispatch into the same
    ``ActionPolicy.evaluate()`` core. POST body: ``{kind, target, params, source,
    reason, actor_id}``. Read-only — runs the same deterministic role/schema/
    precondition/reference invariants ``ActionPolicy.decide()`` enforces for real,
    without writing an ``ActionDecision``/``ActionApproval`` node, so a caller can
    validate a routing payload before proposing it for real execution.
    """
    import json

    from graph_os.gateway.ports import gateway_application
    from graph_os.mcp_server.runtime import _tool_result_response, safe_json_load

    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = str(body.get("kind") or "")
    if not kind:
        return JSONResponse(
            {"status": "error", "message": "kind is required"}, status_code=400
        )
    try:
        res = await gateway_application().execute_tool(
            "graph_governance",
            action="verify_action",
            kind=kind,
            target_id=str(body.get("target") or ""),
            params_json=json.dumps(body.get("params") or {}, default=str),
            source=str(body.get("source") or "manual"),
            reason=str(body.get("reason") or ""),
            actor_id=str(body.get("actor_id") or ""),
        )
        return _tool_result_response(
            "graph_governance", safe_json_load(res), engine_domain=False
        )
    except Exception as exc:  # noqa: BLE001 — canonical safe error surface
        return JSONResponse(public_error_payload(exc, logger=logger), status_code=500)


#: The append-only broker stream fleet events are published to (EG-283/EG-5;
#: writes require the ``fleet:events`` scope per eg-workitem WRAPUP §3d).
_FLEET_EVENTS_STREAM = "fleet.events"
#: Safety bound on one request's full-stream scan (fleet_trace/fleet_touched
#: have no subject/correlation index on an append-only stream — unlike the
#: retired ad-hoc Cypher match, this is a bounded linear scan of the retained
#: log, newest-declared retention window only).
_FLEET_EVENTS_SCAN_MAX_MESSAGES = 200_000


async def _scan_fleet_events(client: Any) -> list[dict[str, Any]]:
    """Read the whole retained ``fleet.events`` stream, decoded, oldest first.

    There is no per-field index on an append-only stream (unlike the retired
    ad-hoc Cypher match), so callers filter/sort the result themselves;
    ``_FLEET_EVENTS_SCAN_MAX_MESSAGES`` bounds one request's scan to the
    stream's own declared retention window.
    """
    events: list[dict[str, Any]] = []
    offset = 0
    scanned = 0
    while scanned < _FLEET_EVENTS_SCAN_MAX_MESSAGES:
        batch = await client.broker.stream_read(
            _FLEET_EVENTS_STREAM, from_offset=offset, max=1000
        )
        if not batch:
            break
        for _msg_offset, raw in batch:
            scanned += 1
            try:
                event = msgpack.unpackb(raw, raw=False)
            except Exception:  # noqa: BLE001 — one malformed message never sinks the scan
                continue
            if isinstance(event, dict):
                events.append(event)
        offset = batch[-1][0] + 1
    return events


async def _stream_events_by_correlation(
    client: Any, correlation_id: str, limit: int
) -> list[dict[str, Any]]:
    events = await _scan_fleet_events(client)
    return [e for e in events if e.get("correlation_id") == correlation_id][:limit]


async def fleet_trace(request: Request) -> JSONResponse:
    """Swarm-wide cross-agent correlation query
    (CONCEPT:AU-OS.observability.run-wide-correlation-id).

    ``GET /api/fleet/trace?correlation_id=<cid>`` returns every durable node
    stamped with that correlation id — fleet events, executed tasks, and any
    other effect that carried the id — so "what did this agent run touch?" is
    answerable from the graph instead of only from external traces.
    """
    cid = request.query_params.get("correlation_id")
    if not cid:
        return JSONResponse(
            {"status": "error", "message": "correlation_id is required"},
            status_code=400,
        )
    try:
        limit = min(int(request.query_params.get("limit", 500)), _MAX_PAGE)
    except (TypeError, ValueError):
        limit = 500
    try:
        session, client, claims = await _verified_graph_client("kg:read")
        nodes: list[Any] = []
        with client.use_verified_context(claims):
            cursor = None
            while len(nodes) < limit:
                page = await client.work_items.list(
                    tenant=session.tenant,
                    cursor=cursor,
                    limit=min(100, limit - len(nodes)),
                    metadata_match={"correlation_id": cid},
                )
                nodes.extend(page.get("items") or [])
                cursor = page.get("next_cursor")
                if cursor is None:
                    break
            nodes.extend(await _stream_events_by_correlation(client, cid, limit))
        return JSONResponse(
            {"status": "success", "correlation_id": cid, "nodes": nodes[:limit]}
        )
    except Exception as exc:  # noqa: BLE001 — degrade gracefully when engine cold
        return JSONResponse(public_error_payload(exc, logger=logger), status_code=500)


async def fleet_touched(request: Request) -> JSONResponse:
    """Blast-radius query: which agents/events touched a resource
    (CONCEPT:AU-OS.observability.run-wide-correlation-id).

    ``GET /api/fleet/touched?resource=<id>`` returns the fleet events whose
    subject is that resource, with their correlation ids and originating
    actors — the queryable "who touched X" the supervisory plane previously
    lacked (it existed only in Langfuse traces / ad-hoc Cypher).
    """
    resource = request.query_params.get("resource")
    if not resource:
        return JSONResponse(
            {"status": "error", "message": "resource is required"}, status_code=400
        )
    try:
        _session, client, claims = await _verified_graph_client("kg:read")
        with client.use_verified_context(claims):
            all_events = await _scan_fleet_events(client)
        events = [e for e in all_events if e.get("subject") == resource]
        events.sort(key=lambda e: e.get("received_at") or "", reverse=True)
        events = events[:_MAX_PAGE]
        actors = sorted(
            {e["actor_id"] for e in events if isinstance(e, dict) and e.get("actor_id")}
        )
        return JSONResponse(
            {
                "status": "success",
                "resource": resource,
                "events": events,
                "actors": actors,
            }
        )
    except Exception as exc:  # noqa: BLE001 — degrade gracefully when engine cold
        return JSONResponse(public_error_payload(exc, logger=logger), status_code=500)
