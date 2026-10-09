"""GraphOS's own domain scopes, kept consistent with the generated registry.

GraphOS advertises a handful of scopes it owns itself (fleet event delivery,
Agent WebUI console roles). GRAPHOS-IDENTITY-R002 requires that these use the
engine's shared scope classes and stay consistent with the single generated
scope registry (``epistemic_graph``'s pinned ``contract/scopes.json``) rather
than a separately maintained list. This module is the one place GraphOS
declares them; ``tests/api/test_domain_scopes.py`` is the registry property
test that checks every entry here still matches that generated registry's
name and class.

Not every scope GraphOS code currently checks is registered yet. The ad hoc
``mcp:*`` fleet-supervision scopes (``graph_os.fleet.multiplexer`` and
``graph_os.api.policy.eunomia``) have no entry in the generated registry at
all; that is a known, tracked gap (GRAPHOS-IDENTITY-R020 adds the missing
upstream entries) and is not papered over here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from graph_os.api.registry.eg_binding import load_generated_scopes

#: The five scope classes the engine's generated registry recognizes.
SCOPE_CLASSES = frozenset({"user", "domain", "service-only", "approver", "admin"})


@dataclass(frozen=True, slots=True)
class DomainScope:
    """One GraphOS-owned scope and the class it must carry in the registry."""

    name: str
    scope_class: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("domain scope name must not be empty")
        if self.scope_class not in SCOPE_CLASSES:
            raise ValueError(f"unknown scope class: {self.scope_class!r}")


#: GraphOS-owned scopes currently registered in the generated scope registry.
GRAPHOS_DOMAIN_SCOPES: tuple[DomainScope, ...] = (
    DomainScope("fleet:events", "service-only"),
    DomainScope("webui:admin", "admin"),
    DomainScope("webui:maintainer", "user"),
    DomainScope("webui:reader", "user"),
    DomainScope("webui:user", "user"),
)


def scopes_by_name() -> dict[str, DomainScope]:
    """Return GraphOS's own declared scopes indexed by name."""

    return {scope.name: scope for scope in GRAPHOS_DOMAIN_SCOPES}


def generated_registry_mismatches(
    contract_root: Path | None = None,
) -> dict[str, str]:
    """Return a problem description per scope that drifted from the registry.

    An empty mapping means every scope declared in ``GRAPHOS_DOMAIN_SCOPES``
    still has the same name and class as the pinned EG contract's generated
    registry. ``contract_root`` is for fixture and wheel integration tests;
    production checks the pinned wheel.
    """

    generated = load_generated_scopes(contract_root)
    problems: dict[str, str] = {}
    for scope in GRAPHOS_DOMAIN_SCOPES:
        generated_class = generated.get(scope.name)
        if generated_class is None:
            problems[scope.name] = "missing from the generated scope registry"
        elif generated_class != scope.scope_class:
            problems[scope.name] = (
                f"registry class {generated_class!r} != declared {scope.scope_class!r}"
            )
    return problems
