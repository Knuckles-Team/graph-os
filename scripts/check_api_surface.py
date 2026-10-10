"""Pre-commit gate: fail closed on undisclosed API-surface drift (GRAPHOS-FLEET-R016).

Thin CLI over ``graph_os.api.registry.surface_gate`` (``public_surface`` /
``diff_public_surface``): this script owns no comparison logic of its own,
it only loads the checked-in baseline, projects the live registry through
``public_surface``, and reports the gate's verdict. A deliberate surface
change updates the baseline file in the same commit that changes the
registry, which is what makes the gate pass again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from graph_os.api.registry import diff_public_surface, public_surface

BASELINE = (
    Path(__file__).resolve().parents[1] / "tests" / "api" / "surface_baseline.json"
)


def _load_baseline(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_current() -> dict[str, dict[str, Any]]:
    from graph_os.api.ops.registry_factory import get_registry

    return public_surface(list(get_registry()))


def main() -> int:
    current = _load_current()
    if not BASELINE.exists():
        BASELINE.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"check_api_surface: wrote initial baseline to {BASELINE}")
        return 0
    baseline = _load_baseline(BASELINE)
    drift = diff_public_surface(baseline, current)
    if drift:
        print(
            f"check_api_surface: undisclosed surface drift: {drift.explain()}",
            file=sys.stderr,
        )
        print(
            f"Update {BASELINE} in the same commit if this change is deliberate.",
            file=sys.stderr,
        )
        return 1
    print("check_api_surface: no undisclosed API-surface drift.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
