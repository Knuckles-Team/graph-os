#!/usr/bin/env python3
"""GraphOS's Python wiring gates: entry point for two checks over ``graph_os/``.

``orphans`` (the ``check-orphan-modules`` pre-commit hook, every stage --
this is the one that blocks): a module is an orphan when it is isolated, with
neither production fan-in (nothing in the package imports it) nor production
fan-out (it imports nothing from the package) -- mirroring the retired
``kiss check``'s own ``orphan_module_enabled`` rule. See
``scripts/wiring/orphans.py``.

``unreachable`` (the ``check-unreachable-modules`` pre-commit hook, manual
stage only -- it reports, it does not block CI): starting from the roots a
real process actually uses to start GraphOS (every ``[project.scripts]`` /
``[project.entry-points]`` target in ``pyproject.toml``, plus the top-level
package), which modules does walking every import edge -- static and
dynamic/name-based -- ever reach? A cluster of modules that only import each
other has nonzero fan-in/fan-out (it passes ``orphans``) but can still be
served by no running process; this command lists exactly that staged, not
yet wired, not yet served code so it can be tracked and landed deliberately.
See ``scripts/wiring/reachability.py``.

Both commands need only the standard library (``ast``, ``tomllib``) and run
in a fresh clone with no synced environment.

Usage: ``python3 scripts/check_wiring.py {orphans,unreachable} [--root REPO]``.
Exit 0 = clean, 1 = findings, 2 = the gate could not establish its universe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wiring.discovery import GateError  # noqa: E402
from wiring.orphans import orphans as find_orphans  # noqa: E402
from wiring.reachability import unreachable as find_unreachable  # noqa: E402

_CHECKS = {
    "orphans": (
        find_orphans,
        "module(s) reachable from no other module and reaching none; import "
        "it, add it as an entry point, or delete it.",
    ),
    "unreachable": (
        find_unreachable,
        "module(s) staged work: nothing a declared root reaches imports "
        "them, directly or indirectly. Not served yet -- wire them in, "
        "register them, or delete them; this report does not block CI.",
    ),
}


def _run(check: str, root: Path) -> int:
    finder, message = _CHECKS[check]
    try:
        findings = finder(root)
    except (GateError, SyntaxError, OSError, ValueError) as exc:
        print(f"wiring {check}: CANNOT RUN: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(f"  - {finding}")
    if findings:
        print(f"wiring {check}: FAILED: {len(findings)} {message}")
        return 1
    print(f"wiring {check}: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=sorted(_CHECKS))
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    return _run(args.check, args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
