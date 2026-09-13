#!/usr/bin/env python3
"""Diff-scope filter for `scripts/check_kiss_staged.sh` (Python port).

Adapted from epistemic-graph's `scripts/kiss_diff_scope.py`
(branch `refactor/eg-f56-registry-kiss`, commit 55835df3 — lane F6, not yet
merged to EG `main`) for a pure-Python repository. The coordinator's
instruction was to use THIS diff-scoping behaviour rather than the
whole-file `kiss-changed-rust` hook on EG `main`, since a whole-file report
re-fails a commit for pre-existing debt it never touched.

The attribution ALGORITHM below (which violation counts as caused by THIS
diff) is unchanged from the source: a function-scoped rule counts only if
its enclosing function is NEW or its exact source text changed since HEAD;
a file-/type-aggregate rule (`AGGREGATE_RULES`) counts only if newly crossed
or worsened in magnitude. The ONE reimplemented piece is `function_spans`:
the source locates a Rust `fn NAME` via masked-source regex + brace
balancing (`rust_lexer`, which has no Python-syntax equivalent to port).
Python's own `ast` module gives an EXACT span (`lineno`/`end_lineno`) for a
`def`/`async def` directly — simpler than porting a lexer for a grammar it
was never written to parse, and correct by construction rather than by
regex approximation.

No baseline file, no self-updating count, no allowlist: every comparison is
computed at run time from two ephemeral `kiss check` reports (the staged
tree and the HEAD blob), never from a stored number.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

VIOLATION_RE = re.compile(
    r"^VIOLATION:(?P<rule>[^:]+):(?P<path>[^:]*):(?P<line>\d+):(?P<name>[^:]*):"
    r"\s?(?P<message>.*)$"
)

# Rules whose count is a property of the WHOLE file (or, for
# methods_per_class, of one type's method count summed across every class
# body scattered through the file) rather than of one contiguous function
# body. These are compared by MAGNITUDE instead, keyed by (rule, item_name)
# between the staged and HEAD reports for the same file. Names match KISS
# 0.4.10's own rule identifiers (`kiss rules`), shared across its Python and
# Rust adapters.
AGGREGATE_RULES = frozenset(
    {
        "statements_per_file",
        "lines_per_file",
        "functions_per_file",
        "interface_types_per_file",
        "concrete_types_per_file",
        "imported_names_per_file",
        "methods_per_class",
    }
)

_INT_RE = re.compile(r"\d+")


def function_spans(source: str, name: str) -> list[tuple[int, int, str]]:
    """Every `def`/`async def NAME` occurrence in `source`.

    Returns `(start_line, end_line, text)` in source order (1-indexed,
    inclusive), using the exact span Python's own `ast` reports for a
    `FunctionDef`/`AsyncFunctionDef` -- no lexing or brace-balancing needed.
    A same-named function found N times (nested, or redefined) is matched to
    the N-th same-named function in the other tree by this ordinal position,
    mirroring how the source scanner behaves for Rust.

    An unparsable file (a syntax error in either the staged or HEAD blob)
    yields no spans -- the caller then treats every violation in that file as
    unattributable-by-content, which `_function_is_attributable` resolves
    fail-open (a finding it cannot disprove counts), never fail-closed-silent.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != name:
            continue
        end_line = getattr(node, "end_lineno", None)
        if end_line is None:
            continue
        start_line = node.lineno
        if node.decorator_list:
            start_line = min(start_line, node.decorator_list[0].lineno)
        text = "".join(lines[start_line - 1 : end_line])
        spans.append((start_line, end_line, text))
    spans.sort(key=lambda span: span[0])
    return spans


def _first_int(message: str) -> int | None:
    match = _INT_RE.search(message)
    return int(match.group()) if match else None


def parse_report(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    violations = []
    for line in text.splitlines():
        match = VIOLATION_RE.match(line)
        if match:
            violations.append(match.groupdict())
    return violations


def _select_span(
    spans: list[tuple[int, int, str]], line: int
) -> tuple[int, tuple[int, int, str]] | None:
    """The span containing `line`, plus its ordinal position among `spans`."""

    for index, span in enumerate(spans):
        start_line, end_line, _ = span
        if start_line <= line <= end_line:
            return index, span
    return None


def _head_aggregate_magnitudes(
    head_violations: list[dict[str, str]],
) -> dict[tuple[str, str], int]:
    """The largest reported count per (rule, item_name), for aggregate rules only."""

    magnitudes: dict[tuple[str, str], int] = {}
    for violation in head_violations:
        if violation["rule"] not in AGGREGATE_RULES:
            continue
        magnitude = _first_int(violation["message"])
        if magnitude is None:
            continue
        key = (violation["rule"], violation["name"])
        magnitudes[key] = max(magnitudes.get(key, -1), magnitude)
    return magnitudes


def _aggregate_is_attributable(
    violation: dict[str, str], head_magnitudes: dict[tuple[str, str], int]
) -> bool:
    """A file-/type-aggregate finding counts iff newly crossed or worsened."""

    head_magnitude = head_magnitudes.get((violation["rule"], violation["name"]))
    if head_magnitude is None:
        return True  # rule absent from the HEAD report: newly crossed
    magnitude = _first_int(violation["message"])
    return magnitude is not None and magnitude > head_magnitude


class _SpanLookup:
    """Cached, by-name `function_spans` lookup over one source tree."""

    def __init__(self, source: str | None) -> None:
        self._source = source
        self._cache: dict[str, list[tuple[int, int, str]]] = {}

    def __call__(self, name: str) -> list[tuple[int, int, str]]:
        if self._source is None:
            return []
        if name not in self._cache:
            self._cache[name] = function_spans(self._source, name)
        return self._cache[name]


def _function_is_attributable(
    violation: dict[str, str], staged_spans: _SpanLookup, head_spans: _SpanLookup
) -> bool:
    """A function-scoped finding counts iff the enclosing item is new or changed."""

    selected = _select_span(staged_spans(violation["name"]), int(violation["line"]))
    if selected is None:
        # Defensive: KISS reported this rule at a line the extractor could
        # not re-locate in the staged source (or the file failed to parse).
        # Fail closed rather than silently drop a finding.
        return True

    ordinal, (_, _, staged_text) = selected
    candidates = head_spans(violation["name"])
    if ordinal >= len(candidates):
        return True  # no same-named function at HEAD: newly added
    return candidates[ordinal][2] != staged_text  # same name, body changed?


def attributable_violations(
    staged_source: str,
    staged_report: str,
    head_source: str | None,
    head_report: str | None,
) -> list[dict[str, str]]:
    """The subset of `staged_report`'s violations the staged diff caused.

    `head_source`/`head_report` are `None` for a file with no HEAD blob (a
    newly added file) -- every staged violation is then attributable, since
    there is no prior state to have been pre-existing debt against.
    """

    staged_violations = parse_report(staged_report)
    if head_source is None:
        return staged_violations

    head_magnitudes = _head_aggregate_magnitudes(parse_report(head_report))
    staged_spans = _SpanLookup(staged_source)
    head_spans = _SpanLookup(head_source)

    return [
        violation
        for violation in staged_violations
        if (
            _aggregate_is_attributable(violation, head_magnitudes)
            if violation["rule"] in AGGREGATE_RULES
            else _function_is_attributable(violation, staged_spans, head_spans)
        )
    ]


def _format(violation: dict[str, str]) -> str:
    return (
        f"VIOLATION:{violation['rule']}:{violation['path']}:{violation['line']}:"
        f"{violation['name']}: {violation['message']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-source", required=True, type=Path)
    parser.add_argument("--staged-report", required=True, type=Path)
    parser.add_argument("--head-source", type=Path)
    parser.add_argument("--head-report", type=Path)
    args = parser.parse_args(argv)

    staged_source = args.staged_source.read_text(encoding="utf-8")
    staged_report = args.staged_report.read_text(encoding="utf-8")
    head_source = (
        args.head_source.read_text(encoding="utf-8")
        if args.head_source is not None and args.head_source.is_file()
        else None
    )
    head_report = (
        args.head_report.read_text(encoding="utf-8")
        if args.head_report is not None and args.head_report.is_file()
        else None
    )

    for violation in attributable_violations(
        staged_source, staged_report, head_source, head_report
    ):
        print(_format(violation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
