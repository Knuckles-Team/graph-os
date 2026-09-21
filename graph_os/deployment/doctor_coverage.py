"""Ingestion, connector, workspace, and unified-install checks."""

from __future__ import annotations

import stat
from types import SimpleNamespace
from typing import Any

from .doctor_support import _result


def _ingestion_freshness(backend: Any) -> dict[str, str]:
    """Last-delta freshness per repo, best-effort: an unavailable manifest is {}."""
    freshness: dict[str, str] = {}
    try:
        from agent_utilities.knowledge_graph.ingestion.manifest import DeltaManifest

        dm = DeltaManifest(backend=backend)
        for cat in ("codebase", "codebase_file"):
            freshness.update(dm.freshness("agent_graph", cat))
    except Exception:  # noqa: BLE001 — freshness is best-effort
        return {}
    return freshness


def _ingestion_coverage_result(rep: dict[str, Any]) -> dict[str, Any]:
    """Turn one coverage assessment into the doctor verdict + aggregate data."""
    missing_count = len(rep["missing"])
    stale_count = len(rep["stale"])
    error_count = len(rep["errors"])
    data = {
        "total": rep["total"],
        "covered": rep["covered"],
        "missing_count": missing_count,
        "stale_count": stale_count,
        "error_count": error_count,
        "coverage_pct": rep["coverage_pct"],
        "total_symbols": rep["total_symbols"],
        "sla_days": rep["sla_days"],
        "redacted": True,
    }
    detail = (
        f"{rep['covered']}/{rep['total']} agent-packages repos ingested "
        f"({rep['coverage_pct']}%), {rep['total_symbols']} symbols"
    )
    if missing_count:
        detail += f", {missing_count} missing"
    if stale_count:
        detail += f", {stale_count} stale (>{rep['sla_days']}d)"
    if error_count:
        detail += f", {error_count} query error(s)"
    if missing_count or stale_count or error_count:
        # A repo-level query failure (D-28) is at least as actionable as a
        # missing repo — never let it silently pass as "ok". It also already
        # lowers coverage_pct (errored repos are excluded from "covered"),
        # so no separate severity rule is needed here.
        status = "fail" if rep["coverage_pct"] < 75 else "warn"
        return _result(
            "ingestion_coverage",
            status,
            detail,
            remediation="`source_sync source=all mode=delta` to ingest or refresh configured repositories",
            skill="graph-ingestion-and-integration",
            data=data,
        )
    return _result("ingestion_coverage", "ok", detail, data=data)


def _check_ingestion_coverage() -> dict[str, Any]:
    """Assert the agent-packages repos are ingested + fresh (CONCEPT:AU-OS.deployment.flagging-repos).

    Native codebase-context-via-KG requires the index to be reliably populated:
    if a repo has no ``:Code`` symbols (or its last delta sync is stale) a KG code
    query returns nothing and the agent silently falls back to grep. This compares
    ``workspace.yml``'s agent-packages subtree against the live KG + DeltaManifest
    freshness, so coverage gaps are visible rather than silent (GAP 1). Repository
    identities remain internal; the doctor result contains aggregate counts only."""
    try:
        from agent_utilities.knowledge_graph.ingestion.coverage import (
            assess_coverage,
            enumerate_agent_packages_repos,
            find_workspace_manifest,
            repo_symbol_counts,
        )

        manifest = find_workspace_manifest()
        if manifest is None:
            return _result(
                "ingestion_coverage",
                "skip",
                "workspace.yml not found (not a workspace checkout)",
            )
        repos = enumerate_agent_packages_repos(manifest)
        if not repos:
            return _result(
                "ingestion_coverage", "skip", "no agent-packages repos in workspace.yml"
            )
        from agent_utilities.knowledge_graph.backends import get_active_backend

        backend = get_active_backend()
        counts, count_errors = repo_symbol_counts(backend, repos)
    except Exception as exc:  # noqa: BLE001
        return _result(
            "ingestion_coverage",
            "skip",
            f"coverage probe unavailable ({type(exc).__name__})",
        )

    freshness = _ingestion_freshness(backend)
    return _ingestion_coverage_result(
        assess_coverage(repos, counts, freshness, errors=count_errors)
    )


def _check_connector_coverage() -> dict[str, Any]:
    """Assert every configured connector is ingesting + fresh (CONCEPT:AU-OS.deployment.connector-coverage-check).

    The connector analogue of ``ingestion_coverage``: a dark or stale connector
    means the world-model for that domain (tickets, deploys, processes…) is silently
    wrong and the agent falls back to hitting the source system. Compares the
    expected connector set against their ``DeltaManifest`` watermarks. Connector
    identities remain internal; the doctor result contains aggregate counts only."""
    try:
        from agent_utilities.knowledge_graph.backends import get_active_backend
        from agent_utilities.knowledge_graph.ingestion.connector_coverage import (
            CONNECTOR_CATEGORY,
            assess_connector_coverage,
            enumerate_expected_connectors,
        )
        from agent_utilities.knowledge_graph.ingestion.manifest import DeltaManifest

        expected = enumerate_expected_connectors()
        if not expected:
            return _result("connector_coverage", "skip", "no connectors configured")
        backend = get_active_backend()
        dm = DeltaManifest(backend=backend)
        freshness: dict[str, str] = {}
        for graph in ("agent_graph", "__commons__"):
            freshness.update(dm.freshness(graph, CONNECTOR_CATEGORY))
    except Exception as exc:  # noqa: BLE001
        return _result(
            "connector_coverage",
            "skip",
            f"connector probe unavailable ({type(exc).__name__})",
        )

    rep = assess_connector_coverage(expected, freshness)
    missing_count = len(rep["missing"])
    stale_count = len(rep["stale"])
    data = {
        "total": rep["total"],
        "covered": rep["covered"],
        "missing_count": missing_count,
        "stale_count": stale_count,
        "coverage_pct": rep["coverage_pct"],
        "sla_days": rep["sla_days"],
        "redacted": True,
    }
    detail = (
        f"{rep['covered']}/{rep['total']} connectors ingesting ({rep['coverage_pct']}%)"
    )
    if missing_count:
        detail += f", {missing_count} dark"
    if stale_count:
        detail += f", {stale_count} stale (>{rep['sla_days']}d)"
    if missing_count or stale_count:
        return _result(
            "connector_coverage",
            "warn",
            detail,
            remediation=(
                "`source_sync source=all mode=delta` to refresh configured sources; "
                "verify their runtime credential references and presets"
            ),
            skill="graph-ingestion-and-integration",
            data=data,
        )
    return _result("connector_coverage", "ok", detail, data=data)


def _check_workspace_config() -> dict[str, Any]:
    """Validate the ``workspace.yml`` repository manifest.

    ``workspace.yml`` is the canonical map of the ecosystem's repositories: the
    bootstrap (``clone_missing_projects``), the read-only project enumeration that
    self-configures KG ingestion breadth (``workspace_project_roots``, KG-2.7), and
    genesis all parse it. A malformed manifest, a repository entry with no ``url``,
    or an incoherent ``subdirectories`` shape silently shrinks what the platform
    clones/ingests — so we validate it through the SAME loader (no re-parse) and
    surface gaps as a doctor finding rather than a silent miss. The manifest path and
    entry-specific validation details never cross the doctor reporting boundary."""
    try:
        from agent_utilities.core.workspace_config import validate_workspace_yml

        rep = validate_workspace_yml()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "workspace_config",
            "skip",
            f"workspace.yml validator unavailable ({type(exc).__name__})",
        )

    if not rep["found"]:
        return _result(
            "workspace_config",
            "skip",
            "no workspace.yml found (not a workspace checkout)",
            remediation=(
                "copy docs/examples/workspace.yml to the workspace root (or the "
                "agent-utilities XDG config dir) and edit it for your repos"
            ),
        )

    data = {
        "found": bool(rep["found"]),
        "parsed": bool(rep["parsed"]),
        "repo_count": int(rep["repo_count"]),
        "error_count": len(rep["errors"]),
        "warning_count": len(rep["warnings"]),
        "redacted": True,
    }
    if rep["errors"]:
        return _result(
            "workspace_config",
            "fail",
            f"workspace.yml has {len(rep['errors'])} validation error(s)",
            remediation=(
                "validate entries against docs/guides/workspace-config.md and the "
                "annotated template in docs/examples/workspace.yml"
            ),
            skill="agent-utilities-deployment",
            data=data,
        )
    detail = f"workspace.yml valid — {rep['repo_count']} repositories"
    if rep["warnings"]:
        nwarn = len(rep["warnings"])
        return _result(
            "workspace_config",
            "warn",
            detail + f", {nwarn} advisory warning(s)",
            remediation="see docs/guides/workspace-config.md for the full schema",
            data=data,
        )
    return _result("workspace_config", "ok", detail, data=data)


def _check_bus() -> dict[str, Any]:
    """Report bus presence, partition-log depth, and unpublished outbox work.

    CONCEPT:AU-ECO.bus.operator-view-agentbus — a growing log or pending send
    outbox means materializers or publishers are not making durable progress.
    """
    try:
        from agent_utilities.core.config import config
        from agent_utilities.knowledge_graph.core.engine import IntelligenceGraphEngine
        from agent_utilities.messaging.bus import AgentBus

        engine = IntelligenceGraphEngine.get_active()
        if engine is None:
            return _result("bus", "skip", "no active engine")
        bus = AgentBus.instance(engine)
        st = bus.status()
        backend_stats = bus._log_backend().stats()
        log_depth = bus._depth_from_stats(backend_stats)
        pending_rows = bus._query(
            "MATCH (o:BusOutbox {status: 'pending'}) RETURN count(o) as n", {}
        )
        pending = int(pending_rows[0].get("n", 0)) if pending_rows else 0
        published_rows = bus._query(
            "MATCH (o:BusOutbox {status: 'published'}) RETURN count(o) as n", {}
        )
        published = int(published_rows[0].get("n", 0)) if published_rows else 0
        warning_depth = max(1, int(config.agent_bus_max_depth * 0.8))
    except Exception as exc:  # noqa: BLE001
        return _result("bus", "skip", f"bus probe unavailable ({type(exc).__name__})")

    detail = (
        f"{st['online']}/{st['agents']} participants online, "
        f"{len(st['topics'])} topics, log depth {log_depth}, "
        f"{pending} pending and {published} unmaterialized outbox record(s)"
    )
    data = {
        **st,
        "log_depth": log_depth,
        "pending_outbox": pending,
        "published_outbox": published,
        "log_backend": backend_stats.get("backend", "unknown"),
    }
    if (
        pending >= warning_depth
        or published >= warning_depth
        or log_depth >= warning_depth
    ):
        return _result(
            "bus",
            "warn",
            detail + " — delivery materializers or publishers are falling behind",
            remediation=(
                "check the configured AgentBus log backend and ensure graph_bus "
                "receivers are draining tenant partitions"
            ),
            data=data,
        )
    return _result("bus", "ok", detail, data=data)


def _check_skills() -> dict[str, Any]:
    """Report whether the agent-utilities skill toolkit is installed in the XDG dir.

    CONCEPT:AU-OS.deployment.agent-factory-autoload — the agent factory loads flat
    operator-owned skills plus valid managed subtrees for current providers under
    ``core.paths.skills_dir()``. The thirteen AU workflow skills unlock the platform. If
    they are absent, point at the one command that installs them. Local discovery
    paths never leave this probe.
    """
    try:
        from agent_utilities.core.providers import (
            _skill_identity,
            resolve_skill_provider_dirs,
        )
        from agent_utilities.skills import BUNDLED_SKILLS

        installed_names = {
            _skill_identity(root) for _provider, root in resolve_skill_provider_dirs()
        }
    except Exception as exc:  # noqa: BLE001
        return _result(
            "skills",
            "fail",
            f"current skill resolution failed ({type(exc).__name__})",
            remediation="reconcile provider registrations and run `agent-utilities install`",
            skill="agent-utilities-deployment",
            data={"ready": False, "redacted": True},
        )

    missing = sorted(set(BUNDLED_SKILLS) - installed_names)
    if missing:
        return _result(
            "skills",
            "warn",
            f"{len(missing)} of {len(BUNDLED_SKILLS)} pre-bundled workflow skills are missing",
            remediation="`agent-utilities install` (installs the thirteen-skill workflow toolkit)",
            skill="agent-utilities-deployment",
            data={"installed": len(installed_names), "missing": missing},
        )
    return _result(
        "skills",
        "ok",
        f"all {len(BUNDLED_SKILLS)} pre-bundled workflow skills are installed",
        data={"installed": len(installed_names), "required": len(BUNDLED_SKILLS)},
    )


def _unified_install_tally() -> SimpleNamespace:
    """Zeroed counters for one unified-install sweep."""
    return SimpleNamespace(
        missing=0,
        unresolved=0,
        materialized=0,
        stale_managed=0,
        unmanaged_nested=0,
        invalid_managed=0,
    )


def _count_generation(
    path: Any,
    provider: str,
    leg: str,
    registration: Any,
    source_manifest: Any,
    tally: SimpleNamespace,
) -> None:
    """Materialized when a managed generation resolves for this provider, else missing."""
    from agent_utilities.core.provider_materialization import (
        resolve_managed_generation,
    )

    resolved = resolve_managed_generation(
        path,
        provider=provider,
        leg=leg,
        registration=registration,
        source_manifest=source_manifest,
    )
    if resolved is not None:
        tally.materialized += 1
    else:
        tally.missing += 1


def _count_provider_materialization(
    registration: Any, root: Any, leg: str, tally: SimpleNamespace
) -> None:
    """Count one registered provider as materialized, missing, or unresolved.

    A source that cannot be read is ``unresolved`` -- never silently skipped and
    never counted as materialized.
    """
    from agent_utilities.core.provider_materialization import (
        ProviderAssetError,
        build_asset_manifest,
    )

    if registration.source_root is None:
        tally.unresolved += 1
        return
    try:
        manifest = build_asset_manifest(
            registration.source_root,
            leg=leg,
            allowed_relative_paths=registration.owned_paths,
        )
    except (OSError, ProviderAssetError, ValueError):
        tally.unresolved += 1
        return
    _count_generation(
        root / registration.name,
        registration.name,
        leg,
        registration.digest,
        manifest,
        tally,
    )


def _count_own_provider(root: Any, leg: str, tally: SimpleNamespace) -> None:
    """Count the hub's OWN contribution for one leg."""
    from agent_utilities.core.provider_materialization import ProviderAssetError
    from agent_utilities.core.unified_install import OWN_PROVIDER, own_provider_asset

    try:
        _source, own_digest, own_manifest = own_provider_asset(leg)
    except (OSError, ProviderAssetError, ValueError):
        tally.unresolved += 1
        return
    _count_generation(
        root / OWN_PROVIDER, OWN_PROVIDER, leg, own_digest, own_manifest, tally
    )


def _nested_child_is_plain_dir(child: Any, tally: SimpleNamespace) -> bool:
    """A leg-root child must be a real directory; anything else is invalid_managed."""
    try:
        child_info = child.lstat()
    except OSError:
        tally.invalid_managed += 1
        return False
    is_junction = getattr(child, "is_junction", lambda: False)()
    if child.is_symlink() or is_junction or not stat.S_ISDIR(child_info.st_mode):
        tally.invalid_managed += 1
        return False
    return True


def _classify_managed_marker(
    child: Any, leg: str, names: set[str], has_marker: bool, tally: SimpleNamespace
) -> None:
    """Classify a nested directory from its provider-ownership marker."""
    from agent_utilities.core.provider_materialization import (
        read_managed_provider_marker,
    )

    marker = read_managed_provider_marker(child, provider=child.name, leg=leg)
    if marker is None:
        if has_marker:
            tally.invalid_managed += 1
        else:
            tally.unmanaged_nested += 1
    elif child.name not in names:
        tally.stale_managed += 1


def _classify_nested_child(
    child: Any, leg: str, names: set[str], tally: SimpleNamespace
) -> None:
    """Classify one directory under a leg root; an unmarked skill folder is fine."""
    from agent_utilities.core.provider_materialization import marker_path_exists

    if not _nested_child_is_plain_dir(child, tally):
        return
    has_marker = marker_path_exists(child)
    if leg == "skills" and not has_marker and (child / "SKILL.md").is_file():
        return
    _classify_managed_marker(child, leg, names, has_marker, tally)


def _scan_nested_children(
    root: Any, leg: str, names: set[str], tally: SimpleNamespace
) -> None:
    """Classify every non-dotfile child under one materialized leg root."""
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.name.startswith("."):
            continue
        _classify_nested_child(child, leg, names, tally)


def _sweep_install_leg(
    leg: str, root: Any, registrations: Any, tally: SimpleNamespace
) -> int:
    """Count one leg's providers and nested children; returns its expected count."""
    from agent_utilities.core.unified_install import OWN_PROVIDER

    names = {item.name for item in registrations}
    names.add(OWN_PROVIDER)
    for registration in registrations:
        if registration.name == OWN_PROVIDER:
            continue
        _count_provider_materialization(registration, root, leg, tally)
    _count_own_provider(root, leg, tally)
    _scan_nested_children(root, leg, names, tally)
    return len(names)


def _unified_install_result(
    legs: dict[str, tuple[Any, Any]],
    expected_counts: dict[str, int],
    tally: SimpleNamespace,
) -> dict[str, Any]:
    """The unified-install verdict; anything unreconciled is never reported ok."""
    data = {
        # Readiness is reportable; machine-specific XDG locations are not.  A
        # doctor result can itself be exported as telemetry, so never place a
        # host filesystem reference in its structured payload.
        "roots_ready": {leg: root.is_dir() for leg, (_g, root) in legs.items()},
        "expected_counts": expected_counts,
        "missing": tally.missing,
        "unresolved": tally.unresolved,
        "materialized": tally.materialized,
        "managed_ready": not any(
            (
                tally.missing,
                tally.unresolved,
                tally.stale_managed,
                tally.unmanaged_nested,
                tally.invalid_managed,
            )
        ),
        "stale_managed": tally.stale_managed,
        "unmanaged_nested": tally.unmanaged_nested,
        "invalid_managed": tally.invalid_managed,
        "redacted": True,
    }
    if tally.unresolved:
        return _result(
            "unified_install",
            "fail",
            f"current provider sources cannot be validated ({tally.unresolved} issue(s))",
            remediation="repair provider distributions before materialization",
            skill="agent-utilities-deployment",
            data=data,
        )
    issues = (
        tally.missing
        + tally.stale_managed
        + tally.unmanaged_nested
        + tally.invalid_managed
    )
    if issues:
        return _result(
            "unified_install",
            "warn",
            f"unified provider materialization needs reconciliation ({issues} issue(s))",
            remediation=(
                "`agent-utilities install` (materializes current providers, marks "
                "ownership, and prunes removed managed providers)"
            ),
            skill="agent-utilities-deployment",
            data=data,
        )
    return _result(
        "unified_install",
        "ok",
        f"unified XDG tree complete — {tally.materialized} provider contribution(s) materialized",
        data=data,
    )


def _check_unified_install() -> dict[str, Any]:
    """Assert the unified XDG tree exists and matches installed providers (CONCEPT:AU-OS.host.doctor-unified-install).

    ``agent-utilities install`` materializes every provider contribution (skills +
    prompts + ontologies, incl. the hub's OWN under ``agent-utilities``) into one XDG
    data tree the runtime reads from. This flags missing current providers, removed
    managed providers, and unmarked nested directories without reporting their local
    filesystem locations.
    """
    try:
        from agent_utilities.core.paths import ontology_dir, skills_dir

        # Import gate: the helpers below re-import these. Kept here so a partial
        # install still reports `skip` up front, exactly as it did before the
        # split, rather than raising out of the check later.
        from agent_utilities.core.provider_materialization import (  # noqa: F401
            ProviderAssetError,
            build_asset_manifest,
            marker_path_exists,
            read_managed_provider_marker,
            resolve_managed_generation,
        )
        from agent_utilities.core.providers import (
            ONTOLOGY_PROVIDER_GROUP,
            PROMPT_PROVIDER_GROUP,
            SKILL_PROVIDER_GROUP,
            provider_registrations,
        )
        from agent_utilities.core.unified_install import (  # noqa: F401
            OWN_PROVIDER,
            own_provider_asset,
            unified_prompts_dir,
        )
    except Exception as exc:  # noqa: BLE001
        return _result(
            "unified_install",
            "skip",
            f"unified-install probe unavailable ({type(exc).__name__})",
        )

    legs = {
        "skills": (SKILL_PROVIDER_GROUP, skills_dir()),
        "prompts": (PROMPT_PROVIDER_GROUP, unified_prompts_dir()),
        "ontologies": (ONTOLOGY_PROVIDER_GROUP, ontology_dir()),
    }
    tally = _unified_install_tally()
    expected_counts: dict[str, int] = {}
    for leg, (group, root) in legs.items():
        try:
            registrations = provider_registrations(group)
        except Exception as exc:  # noqa: BLE001
            return _result(
                "unified_install",
                "fail",
                f"provider registry invalid ({type(exc).__name__})",
                remediation="remove duplicate or invalid provider registrations",
                skill="agent-utilities-deployment",
                data={"ready": False, "redacted": True},
            )
        expected_counts[leg] = _sweep_install_leg(leg, root, registrations, tally)
    return _unified_install_result(legs, expected_counts, tally)
