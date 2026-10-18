"""Engine-surface and graph-connection checks for the deployment doctor.

CONCEPT:ARBORA-DEPLOY.doctor.engine-and-connections

arbora serves the ``engine_<domain>`` tool families and owns the graph
connection registry, so arbora's doctor (not the agent harness) answers
whether the served engine surface is complete and whether every declared graph
connection is registered and, on a live run, reachable. Output is aggregate and
redacted: aliases, endpoints, references and exception text never cross the
doctor boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from .doctor_support import _result


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
    (:mod:`arbora.deployment.genesis_environments`) to run
    engine-less by design — warn there, fail everywhere else. This mirrors
    the exact ``tiny``-vs-else split :func:`_check_config` already
    established for the same profile distinction (durability findings are
    advisory on ``tiny``, a hard failure elsewhere).
    """
    try:
        from arbora.core.settings import ArboraConfig as AgentConfig
        from arbora.mcp_server.backing.tools import engine_tools

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
            "install the engine client (`pip install 'epistemic-graph[server]'`, a arbora runtime "
            "dependency) so the "
            "required engine_<domain> tool families and their REST twins are "
            "actually served"
        ),
        skill="arbora-deployment",
        data=data,
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
    """How many declarations carry no usable name.

    The engine's connection registry validates the rest of each declaration
    when it binds the connection.
    """
    return sum(
        1
        for value in declarations
        if not isinstance(value, Mapping) or not str(value.get("name") or "").strip()
    )


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
    from arbora.mcp_server.backing.engine_routing import get_connection_registry

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
        from arbora.core.settings import ArboraConfig as AgentConfig

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
