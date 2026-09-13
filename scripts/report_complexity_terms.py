#!/usr/bin/env python3
"""Report the CCCC whole-tree census: measured vs. over-cap, no acceptance rule.

Adapted from epistemic-graph's `scripts/report_complexity_terms.py`
(commit 593e7e427194d22aade38d7cc1ae2a98ddc677c8). The source splits an
over-cyclomatic-only function into "accepted by rule" (the Rust exhaustive-
dispatch exemption) vs. real backlog. This repository carries no such
exemption (see `check_complexity_staged.py`'s module docstring — this repo
has zero `.rs` files, so porting the exemption machinery would be permanently
dead code), so there is nothing to split: every function over either cap is
backlog. This is a REPORT, not a gate: it never fails on a count, holds no
threshold, and writes nothing — `check_complexity_staged.py` is what
enforces, on the diff. `_document` delegates to
`scanner_contract.read_json_report`, shared with `validate_cccc_census.py`
(jscpd flagged the two files' identical report-loading preamble as a new
duplicate on first introduction here; see that function's docstring).

Exit codes: 0 printed a report, 2 the report could not be produced (an
ENVIRONMENT fact -- never reported as "no findings").
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scanner_contract import (  # noqa: E402
    CCCC_MAX_COGNITIVE,
    CCCC_MAX_CYCLOMATIC,
    read_json_report,
)


def fail(message: str) -> NoReturn:
    print(f"complexity terms: CANNOT RUN: {message}", file=sys.stderr)
    raise SystemExit(2)


def _rows(document: dict[str, Any]) -> list[tuple[str, str, int, int, int]]:
    files = document.get("files")
    if not isinstance(files, list) or not files:
        fail("report has no files array")
    out: list[tuple[str, str, int, int, int]] = []

    def walk(fn: object, path: str, prefix: str) -> None:
        if not isinstance(fn, dict):
            fail("report contains a function that is not an object")
        for field in ("name", "line", "cyclomatic", "cognitive"):
            if not isinstance(fn.get(field), (str, int)) or isinstance(
                fn.get(field), bool
            ):
                fail(f"report contains a function without a valid {field}")
        name = f"{prefix}{fn['name']}"
        out.append((name, path, int(fn["line"]), fn["cyclomatic"], fn["cognitive"]))
        children = fn.get("children", [])
        if not isinstance(children, list):
            fail(f"report function {name} has invalid children")
        for kid in children:
            walk(kid, path, f"{name}.")

    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            fail("report contains a file without a path")
        functions = entry.get("functions")
        if not isinstance(functions, list):
            fail(f"report file {entry['path']} has no functions array")
        for fn in functions:
            walk(fn, entry["path"], "")
    return out


def _document(path: Path) -> dict[str, Any]:
    return read_json_report(path, fail)


def report(path: Path) -> int:
    rows = _rows(_document(path))
    over = [
        row
        for row in rows
        if row[3] > CCCC_MAX_CYCLOMATIC or row[4] > CCCC_MAX_COGNITIVE
    ]
    print(
        f"complexity terms: {len(rows)} function(s) measured, "
        f"{len(over)} over cyclomatic {CCCC_MAX_CYCLOMATIC} "
        f"or cognitive {CCCC_MAX_COGNITIVE}"
    )
    print(f"  REAL BACKLOG              {len(over):5d}")
    return len(over)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        fail("usage: report_complexity_terms.py REPORT.json")
    report(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
