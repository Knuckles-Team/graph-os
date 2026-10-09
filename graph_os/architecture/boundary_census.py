"""Import-boundary census for GRAPHOS-HOST-R001.

Loads the boundary seeds published in
``architecture/component-registry-boundary-seeds.yml`` (GRAPHOS-HOST-R012) and
exposes a typed model that a continuous-integration check can use to confirm
that every external dependency ``graph_os`` imports from an owner repository
is reached only through that owner's published ``public_import_surface`` --
never an internal module of that dependency.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_SEEDS_PATH = (
    Path(__file__).resolve().parents[2]
    / "architecture"
    / "component-registry-boundary-seeds.yml"
)


@dataclass(frozen=True)
class BoundaryOwner:
    """One owner repository's declared public import surface."""

    owner_repository: str
    owned_module_paths: tuple[str, ...]
    public_import_surface: tuple[str, ...]

    def covers(self, dotted_import: str) -> bool:
        """True if ``dotted_import`` resolves within this owner's public surface."""
        return any(
            dotted_import == surface or dotted_import.startswith(surface + ".")
            for surface in self.public_import_surface
        )

    def owns(self, dotted_import: str) -> bool:
        """True if ``dotted_import`` falls under one of this owner's module paths."""
        slashed = dotted_import.replace(".", "/")
        return any(
            slashed == path
            or slashed.startswith(path + "/")
            or slashed.startswith(path + ".")
            for path in self.owned_module_paths
        )


@dataclass(frozen=True)
class BoundaryViolation:
    """A single import statement that reaches an owner outside its public surface."""

    source_file: Path
    imported: str
    owner_repository: str


class ImportBoundaryCensus:
    """Typed census of boundary owners, loaded from the published seeds file."""

    def __init__(self, owners: tuple[BoundaryOwner, ...]) -> None:
        self._owners = owners

    @classmethod
    def from_seeds_file(cls, path: Path = DEFAULT_SEEDS_PATH) -> ImportBoundaryCensus:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        owners: dict[str, list[BoundaryOwner]] = {}
        for seed in data.get("seeds", []):
            owner = BoundaryOwner(
                owner_repository=seed["owner_repository"],
                owned_module_paths=tuple(seed.get("owned_module_paths", ())),
                public_import_surface=tuple(seed.get("public_import_surface", ())),
            )
            owners.setdefault(owner.owner_repository, []).append(owner)
        merged = tuple(
            BoundaryOwner(
                owner_repository=repo,
                owned_module_paths=tuple(
                    p for o in entries for p in o.owned_module_paths
                ),
                public_import_surface=tuple(
                    s for o in entries for s in o.public_import_surface
                ),
            )
            for repo, entries in owners.items()
        )
        return cls(merged)

    def owner_for(self, dotted_import: str) -> BoundaryOwner | None:
        for owner in self._owners:
            if owner.owns(dotted_import):
                return owner
        return None

    def census(self, source_file: Path) -> list[BoundaryViolation]:
        """Return every import in ``source_file`` that escapes its owner's surface."""
        tree = ast.parse(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        violations: list[BoundaryViolation] = []
        for node in ast.walk(tree):
            dotted: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dotted = alias.name
                    self._check(source_file, dotted, violations)
            elif isinstance(node, ast.ImportFrom) and node.module:
                dotted = node.module
                self._check(source_file, dotted, violations)
        return violations

    def _check(
        self, source_file: Path, dotted: str, violations: list[BoundaryViolation]
    ) -> None:
        owner = self.owner_for(dotted)
        if owner is None:
            return
        if owner.owner_repository == "graph-os":
            return
        if not owner.covers(dotted):
            violations.append(
                BoundaryViolation(
                    source_file=source_file,
                    imported=dotted,
                    owner_repository=owner.owner_repository,
                )
            )


def main() -> int:
    """CI entry point: census every ``.py`` file under ``graph_os/``."""
    census = ImportBoundaryCensus.from_seeds_file()
    graph_os_root = DEFAULT_SEEDS_PATH.parents[1] / "graph_os"
    violations = [
        violation
        for source_file in sorted(graph_os_root.rglob("*.py"))
        for violation in census.census(source_file)
    ]
    for violation in violations:
        print(
            f"{violation.source_file}: import of '{violation.imported}' "
            f"escapes {violation.owner_repository}'s public import surface",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
