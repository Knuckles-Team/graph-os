"""Truthful fleet-supervision evidence (ARBORA-FLEET-R750).

The supervisory REST and MCP surfaces read the same operational authorities:
the durable session/control store, the engine-backed goal registry, and the
dispatch-worker registry.  The caller injects each authority as a reader in
:class:`FleetAuthorities`; an authority with no reader is reported as
``unavailable`` (not served), never as an empty result.  A failed read is *not* an empty result.  This module
owns the one typed envelope that records what was actually read, how fresh that
evidence is, and whether a caller may safely use it for readiness, convergence,
or scaling decisions.

The module deliberately keeps no process-local health cache.  ``last_success_at``
is the timestamp of the successful read represented by the envelope (or
``None`` when no read succeeded); durable data remains authoritative in the
existing stores.  Diagnostics contain only bounded exception type names and
stable labels, never exception text, paths, URLs, credentials, or row values.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

#: ``not_served``: this release has no reader for the authority (AuthorityNotServed).
#: It is "not applicable", never a failure, and never gates readiness.
FleetDependencyState = Literal[
    "healthy", "partial", "degraded", "unavailable", "not_served"
]
FleetHealthState = Literal[
    "healthy", "partial", "degraded", "unavailable", "not_served"
]


@dataclass(frozen=True)
class FleetAuthorities:
    """Readers for the three supervisory authorities.

    ``connect`` opens a DB-API connection to the session/control store (with a
    ``dialect`` attribute).  ``goals`` returns ``{"active": n, "tracked": n}``
    from the live goal authority and raises when it cannot.  ``workers``
    returns the dispatch-worker registry rows.  ``None`` (or a reader raising
    :class:`AuthorityNotServed`) means the authority is not served in this
    process: it reports ``not_served`` and does not gate readiness.
    """

    connect: Callable[[], Any] | None = None
    goals: Callable[[], dict[str, Any]] | None = None
    workers: Callable[[], list[dict[str, Any]]] | None = None


class AuthorityNotServed(RuntimeError):
    """A supervisory authority has no reader in this process."""


class FleetHealthPayload(TypedDict):
    """Transport-neutral fleet health response contract."""

    generated_at: float
    sessions: dict[str, Any] | None
    goals: dict[str, Any] | None
    domains: dict[str, dict[str, float]] | None
    dispatch_workers: list[dict[str, Any]] | None
    evidence: dict[str, Any]


_MAX_DIAGNOSTICS = 4
_MAX_DIAGNOSTIC_LENGTH = 192
_ERROR_STATUSES = ("failed", "error", "cancelled")
_ACTIVE_STATUSES = ("active", "running")


def _domain_sql(dialect: str) -> str:
    """Derive a session domain using the state store's SQL dialect."""

    if dialect == "postgres":

        def j(key: str) -> str:
            return f"NULLIF(metadata_json::jsonb ->> '{key}', '')"

        valid = "pg_input_is_valid(metadata_json, 'jsonb')"
    else:

        def j(key: str) -> str:
            return f"NULLIF(json_extract(metadata_json, '$.{key}'), '')"

        valid = "json_valid(metadata_json)"
    coalesce = f"COALESCE({j('domain')}, {j('team')}, {j('tenant')}, 'default')"
    return f"CASE WHEN {valid} THEN {coalesce} ELSE 'default' END"


def _sql_in(values: tuple[str, ...]) -> str:
    """Render the fixed status literals used by the aggregate query."""

    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _diagnostic(label: str, exc: BaseException) -> str:
    """Return a bounded, non-secret diagnostic for a failed dependency read."""

    # Exception messages frequently contain DSNs, local paths, or response
    # bodies.  The type is enough to identify the failure class at this public
    # boundary; the detailed cause remains in the server log.
    return f"{label}: read failed ({type(exc).__name__})"[:_MAX_DIAGNOSTIC_LENGTH]


def _bounded_diagnostics(values: Iterable[str]) -> list[str]:
    return [str(value)[:_MAX_DIAGNOSTIC_LENGTH] for value in values][:_MAX_DIAGNOSTICS]


class FleetDependencyEvidence(BaseModel):
    """Evidence for one dependency read in a fleet snapshot."""

    model_config = ConfigDict(extra="forbid")

    status: FleetDependencyState
    checked_at: float = Field(ge=0)
    last_success_at: float | None = Field(default=None, ge=0)
    freshness_seconds: float | None = Field(default=None, ge=0)
    diagnostics: list[str] = Field(default_factory=list, max_length=_MAX_DIAGNOSTICS)


class FleetHealthEvidence(BaseModel):
    """The shared ``fleet.health.v1`` fail-closed supervisory contract.

    ``healthy`` is the only state that authorizes readiness, autoscaling, or
    desired-state convergence.  ``partial`` means a bounded read returned some
    usable data but at least one required sub-read failed.  ``degraded`` means
    a source returned evidence that is explicitly stale/partially impaired.
    ``unavailable`` means no usable evidence exists for the dependency set.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fleet.health.v1"] = "fleet.health.v1"
    status: FleetHealthState
    ready: bool
    autoscaling_ready: bool
    convergence_ready: bool
    generated_at: float = Field(ge=0)
    last_success_at: float | None = Field(default=None, ge=0)
    freshness_seconds: float | None = Field(default=None, ge=0)
    dependencies: dict[str, FleetDependencyEvidence] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list, max_length=_MAX_DIAGNOSTICS)


@dataclass(frozen=True)
class ControlStoreRead:
    """Typed result of the existing session/control-store SQL reads."""

    status: FleetDependencyState
    checked_at: float
    last_success_at: float | None
    by_status: dict[str, int] | None
    domains: dict[str, dict[str, float]] | None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class FleetHealthSnapshot:
    """Envelope consumed by both supervisory transports and autonomy ticks."""

    evidence: FleetHealthEvidence
    sessions: dict[str, Any] | None
    goals: dict[str, Any] | None
    domains: dict[str, dict[str, float]] | None
    dispatch_workers: list[dict[str, Any]] | None

    def as_dict(self) -> FleetHealthPayload:
        """Serialize the one transport-neutral response shape."""

        return {
            "generated_at": self.evidence.generated_at,
            "sessions": self.sessions,
            "goals": self.goals,
            "domains": self.domains,
            "dispatch_workers": self.dispatch_workers,
            "evidence": self.evidence.model_dump(mode="json"),
        }


def _dependency(
    status: FleetDependencyState,
    checked_at: float,
    last_success_at: float | None,
    diagnostics: Iterable[str] = (),
) -> FleetDependencyEvidence:
    freshness = (
        max(0.0, checked_at - last_success_at) if last_success_at is not None else None
    )
    return FleetDependencyEvidence(
        status=status,
        checked_at=checked_at,
        last_success_at=last_success_at,
        freshness_seconds=freshness,
        diagnostics=_bounded_diagnostics(diagnostics),
    )


def _not_served(label: str, now: float) -> FleetDependencyEvidence:
    return _dependency(
        "not_served", now, None, (f"{label}: not served in this process",)
    )


def _fully_served(
    status: FleetHealthState, dependencies: Iterable[FleetDependencyEvidence]
) -> bool:
    """Autoscaling and convergence need every authority served and healthy."""
    return status == "healthy" and all(d.status != "not_served" for d in dependencies)


def _rollup(dependencies: Iterable[FleetDependencyEvidence]) -> FleetHealthState:
    all_states = [dependency.status for dependency in dependencies]
    # Only served authorities count; an unserved one is not applicable.
    states = [state for state in all_states if state != "not_served"]
    if all_states and not states:
        return "not_served"
    if not states or all(state == "unavailable" for state in states):
        return "unavailable"
    if any(state == "partial" for state in states):
        return "partial"
    if any(state == "unavailable" for state in states):
        # Some authoritative evidence exists, but the fleet cannot be treated
        # as converged while another authority is unreadable.
        return "partial"
    if any(state == "degraded" for state in states):
        return "degraded"
    return "healthy"


def _read_control_store(
    connect: Callable[[], Any] | None,
    *,
    scope_sql: str = "",
    scope_params: Iterable[Any] = (),
    scope_resolver: Callable[[str], tuple[str, Iterable[Any]]] | None = None,
    now: float | None = None,
) -> ControlStoreRead:
    """Read session aggregates from the existing state-store authority.

    Status and domain aggregates are intentionally separate bounded reads.  A
    failure in one therefore becomes ``partial`` evidence instead of being
    laundered into an empty mapping.
    """

    checked_at = time.time() if now is None else float(now)
    by_status: dict[str, int] | None = None
    domains: dict[str, dict[str, float]] | None = None
    diagnostics: list[str] = []
    status_ok = False
    domains_ok = False
    scope_params = tuple(scope_params)
    conn: Any = None
    not_served = False
    try:
        if connect is None:
            raise AuthorityNotServed("control store")
        conn = connect()
        if scope_resolver is not None:
            try:
                scope_sql, scope_params = scope_resolver(str(conn.dialect))
            except PermissionError:
                raise
            scope_params = tuple(scope_params)
        cur = conn.cursor()
        where = f" WHERE {scope_sql}" if scope_sql else ""
        try:
            cur.execute(
                f"SELECT status, COUNT(*) FROM sessions{where} GROUP BY status",  # nosec B608 — where is a dialect predicate from the verified scope resolver
                list(scope_params),
            )
            by_status = {
                str(row[0] or "unknown"): int(row[1]) for row in cur.fetchall()
            }
            status_ok = True
        except Exception as exc:  # noqa: BLE001 - represented in typed evidence
            diagnostics.append(_diagnostic("control_store.sessions", exc))

        try:
            # These values are intentionally the same SQL aggregates used by
            # the historical supervisory handler.  The live state store, not
            # this module, remains the sole authority for the rows.
            dom = _domain_sql(conn.dialect)
            cur.execute(
                f"""
                SELECT {dom} AS domain,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status IN {_sql_in(_ACTIVE_STATUSES)} THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN status IN {_sql_in(_ERROR_STATUSES)} THEN 1 ELSE 0 END) AS errored
                FROM sessions{where} GROUP BY 1
                """,  # nosec B608 — all interpolations are dialect/status constants
                list(scope_params),
            )
            domains = {}
            for row in cur.fetchall():
                total = int(row[1])
                errored = int(row[3])
                domains[str(row[0])] = {
                    "total": total,
                    "active": int(row[2]),
                    "errored": errored,
                    "error_rate": round(errored / total, 4) if total else 0.0,
                }
            domains_ok = True
        except Exception as exc:  # noqa: BLE001 - represented in typed evidence
            diagnostics.append(_diagnostic("control_store.domains", exc))
    except PermissionError:
        # Authentication/tenant failures are transport authorization failures,
        # not dependency health.  Preserve the gateway's existing boundary.
        raise
    except AuthorityNotServed:
        not_served = True
    except Exception as exc:  # noqa: BLE001 - represented in typed evidence
        diagnostics.append(_diagnostic("control_store.connection", exc))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001 - represented in typed evidence
                diagnostics.append(_diagnostic("control_store.close", exc))

    if not_served:
        state: FleetDependencyState = "not_served"
        diagnostics.append("control_store: not served in this process")
    elif status_ok and domains_ok and not diagnostics:
        state = "healthy"
    elif status_ok and domains_ok:
        state = "degraded"
    elif status_ok or domains_ok:
        state = "partial"
    else:
        state = "unavailable"
    return ControlStoreRead(
        status=state,
        checked_at=checked_at,
        last_success_at=checked_at if (status_ok or domains_ok) else None,
        by_status=by_status,
        domains=domains,
        diagnostics=tuple(_bounded_diagnostics(diagnostics)),
    )


def _read_goal_registry(
    goals: Callable[[], dict[str, Any]] | None, now: float
) -> tuple[FleetDependencyEvidence, dict[str, Any] | None]:
    """Probe the live goal authority on every collection; failure is evidence."""

    try:
        if goals is None:
            raise AuthorityNotServed("goal authority")
        counts = goals()
        return (
            _dependency("healthy", now, now),
            {"active": int(counts["active"]), "tracked": int(counts["tracked"])},
        )
    except AuthorityNotServed:
        return _not_served("goal_rehydration", now), None
    except Exception as exc:  # noqa: BLE001 - represented in typed evidence
        return (
            _dependency(
                "unavailable", now, None, (_diagnostic("goal_rehydration", exc),)
            ),
            None,
        )


def _read_worker_registry(
    workers_reader: Callable[[], list[dict[str, Any]]] | None, now: float
) -> tuple[FleetDependencyEvidence, list[dict[str, Any]] | None]:
    """Read the live dispatch-worker registry; failure never becomes ``[]``."""

    try:
        if workers_reader is None:
            raise AuthorityNotServed("worker registry")
        workers = workers_reader()
        if not isinstance(workers, list):
            raise TypeError("worker registry returned a non-list result")
        return _dependency("healthy", now, now), workers
    except AuthorityNotServed:
        return _not_served("worker_registry", now), None
    except Exception as exc:  # noqa: BLE001 - represented in typed evidence
        return (
            _dependency(
                "unavailable", now, None, (_diagnostic("worker_registry", exc),)
            ),
            None,
        )


def collect_fleet_health(
    authorities: FleetAuthorities,
    *,
    scope_sql: str = "",
    scope_params: Iterable[Any] = (),
    scope_resolver: Callable[[str], tuple[str, Iterable[Any]]] | None = None,
    now: float | None = None,
) -> FleetHealthSnapshot:
    """Collect one fail-closed fleet snapshot from the live authorities.

    ``scope_sql``/``scope_params`` are supplied by the authenticated gateway
    caller.  Internal maintenance callers intentionally use the unrestricted
    process scope; they do not receive a transport payload and therefore do
    not bypass a user/tenant boundary.
    """

    generated_at = time.time() if now is None else float(now)
    control = _read_control_store(
        authorities.connect,
        scope_sql=scope_sql,
        scope_params=scope_params,
        scope_resolver=scope_resolver,
        now=generated_at,
    )
    goal_dependency, goals = _read_goal_registry(authorities.goals, generated_at)
    worker_dependency, workers = _read_worker_registry(
        authorities.workers, generated_at
    )
    control_dependency = _dependency(
        control.status,
        control.checked_at,
        control.last_success_at,
        control.diagnostics,
    )
    dependencies = {
        "goal_rehydration": goal_dependency,
        "control_store": control_dependency,
        "worker_registry": worker_dependency,
    }
    status = _rollup(dependencies.values())
    diagnostics = _bounded_diagnostics(
        f"{name}: {message}"
        for name, dependency in dependencies.items()
        for message in dependency.diagnostics
    )
    last_success_at = max(
        (
            dependency.last_success_at
            for dependency in dependencies.values()
            if dependency.last_success_at is not None
        ),
        default=None,
    )
    evidence = FleetHealthEvidence(
        status=status,
        # Readiness: every served authority is healthy (none served is ready).
        ready=status in ("healthy", "not_served"),
        autoscaling_ready=_fully_served(status, dependencies.values()),
        convergence_ready=_fully_served(status, dependencies.values()),
        generated_at=generated_at,
        last_success_at=last_success_at,
        freshness_seconds=(
            max(0.0, generated_at - last_success_at)
            if last_success_at is not None
            else None
        ),
        dependencies=dependencies,
        diagnostics=diagnostics,
    )
    sessions = None
    if control.by_status is not None:
        sessions = {
            "total": sum(control.by_status.values()),
            "by_status": control.by_status,
        }
    return FleetHealthSnapshot(
        evidence=evidence,
        sessions=sessions,
        goals=goals,
        domains=control.domains,
        dispatch_workers=workers,
    )


def health_payload(snapshot: FleetHealthSnapshot) -> FleetHealthPayload:
    """Return the canonical REST/MCP payload for a snapshot."""

    return snapshot.as_dict()


def unavailable_fleet_health(
    label: str, *, now: float | None = None
) -> FleetHealthEvidence:
    """Build one typed unavailable result for an autonomy-provider failure."""

    checked_at = time.time() if now is None else float(now)
    diagnostic = _diagnostic(label, RuntimeError())
    dependencies = {
        name: _dependency("unavailable", checked_at, None, (diagnostic,))
        for name in ("goal_rehydration", "control_store", "worker_registry")
    }
    return FleetHealthEvidence(
        status="unavailable",
        ready=False,
        autoscaling_ready=False,
        convergence_ready=False,
        generated_at=checked_at,
        dependencies=dependencies,
        diagnostics=[diagnostic],
    )


def mark_dependency_failure(
    snapshot: FleetHealthSnapshot,
    dependency_name: str,
    label: str,
    exc: BaseException,
) -> FleetHealthSnapshot:
    """Add one bounded late-read failure to an existing typed snapshot."""

    generated_at = time.time()
    previous = snapshot.evidence.dependencies.get(dependency_name)
    diagnostic = _diagnostic(label, exc)
    previous_status = previous.status if previous is not None else "unavailable"
    dependency_status: FleetDependencyState = (
        "partial" if previous_status in {"healthy", "degraded"} else "unavailable"
    )
    dependency = _dependency(
        dependency_status,
        generated_at,
        previous.last_success_at if previous is not None else None,
        (*((previous.diagnostics) if previous is not None else ()), diagnostic),
    )
    dependencies = dict(snapshot.evidence.dependencies)
    dependencies[dependency_name] = dependency
    status = _rollup(dependencies.values())
    last_success_at = max(
        (
            item.last_success_at
            for item in dependencies.values()
            if item.last_success_at is not None
        ),
        default=None,
    )
    evidence = snapshot.evidence.model_copy(
        update={
            "status": status,
            "ready": status in ("healthy", "not_served"),
            "autoscaling_ready": _fully_served(status, dependencies.values()),
            "convergence_ready": _fully_served(status, dependencies.values()),
            "generated_at": generated_at,
            "last_success_at": last_success_at,
            "freshness_seconds": (
                max(0.0, generated_at - last_success_at)
                if last_success_at is not None
                else None
            ),
            "dependencies": dependencies,
            "diagnostics": _bounded_diagnostics(
                f"{name}: {message}"
                for name, item in dependencies.items()
                for message in item.diagnostics
            ),
        }
    )
    return FleetHealthSnapshot(
        evidence=evidence,
        sessions=snapshot.sessions,
        goals=snapshot.goals,
        domains=snapshot.domains,
        dispatch_workers=snapshot.dispatch_workers,
    )


__all__ = [
    "AuthorityNotServed",
    "ControlStoreRead",
    "FleetAuthorities",
    "FleetDependencyEvidence",
    "FleetDependencyState",
    "FleetHealthEvidence",
    "FleetHealthState",
    "FleetHealthSnapshot",
    "_domain_sql",
    "_sql_in",
    "collect_fleet_health",
    "health_payload",
    "mark_dependency_failure",
    "unavailable_fleet_health",
]
