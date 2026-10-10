"""Pre-commit gate: fail closed on backward-incompatible narrowing (GRAPHOS-FLEET-R016).

Thin CLI over ``graph_os.api.registry.surface_gate.diff_backward_compat``:
the baseline only needs the two fields that gate inspects duck-typed
(``scopes`` and ``principals``), so the checked-in fixture stores that
narrow projection rather than a full, unserializable ``OpSpec``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graph_os.api.registry import OpSpec, PrincipalRule, diff_backward_compat

BASELINE = (
    Path(__file__).resolve().parents[1] / "tests" / "api" / "compat_baseline.json"
)


@dataclass(frozen=True)
class _BaselineOp:
    """Duck-typed baseline projection: only what ``diff_backward_compat`` reads."""

    scopes: frozenset[str]
    principals: PrincipalRule


def _load_baseline(path: Path) -> dict[str, _BaselineOp]:
    if not path.exists():
        return {}
    raw: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return {
        op_id: _BaselineOp(
            scopes=frozenset(entry["scopes"]),
            principals=PrincipalRule(entry["principals"]),
        )
        for op_id, entry in raw.items()
    }


def _current_ops() -> dict[str, OpSpec]:
    from graph_os.api.ops.registry_factory import get_registry

    return {op.id: op for op in get_registry()}


def _serialize(current: dict[str, OpSpec]) -> dict[str, dict[str, Any]]:
    return {
        op_id: {"scopes": sorted(op.scopes), "principals": op.principals.value}
        for op_id, op in current.items()
    }


def main() -> int:
    current = _current_ops()
    if not BASELINE.exists():
        BASELINE.write_text(
            json.dumps(_serialize(current), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"check_api_compat: wrote initial baseline to {BASELINE}")
        return 0
    baseline = _load_baseline(BASELINE)
    # `diff_backward_compat` only reads `.scopes`/`.principals` off the baseline
    # values, which `_BaselineOp` supplies duck-typed; a real `OpSpec` isn't
    # reconstructable from the serialized JSON fixture.
    result = diff_backward_compat(baseline, current)  # type: ignore[arg-type]
    if result:
        print(
            f"check_api_compat: backward-incompatible narrowing: {result.explain()}",
            file=sys.stderr,
        )
        return 1
    print("check_api_compat: no backward-incompatible narrowing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
