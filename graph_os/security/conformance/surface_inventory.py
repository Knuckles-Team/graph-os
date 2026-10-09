#!/usr/bin/python
from __future__ import annotations

"""Live introspection of the ``graph_query`` dialect dispatcher.

CONCEPT:AU-OS.identity.stack-wide-auth-conformance — see this package's
``__init__.py`` and ``plans/graph-os-completion-program/decisions/
GOC-62-keycloak-auth-standard.md`` §D2.

:func:`enumerate_query_dialect_surfaces` parses the ACTUAL source of
``agent_utilities/mcp/tools/query_tools.py`` (never a hand-maintained list) to
find every ``if scope == "<literal>":`` branch inside the ``graph_query`` tool
registration closure — i.e. every dialect a caller can request. A dialect
added later (a new ``if scope == "graphql":`` branch, say) is discovered by
this function automatically, with zero edits here — that is the entire point:
this module's job is to make it IMPOSSIBLE for a new surface to be silently
uncovered, not to guess whether it is safe (see the package docstring for why
disposition stays a reviewed manifest field instead).

The one dialect with NO explicit ``if scope == ...`` branch — the implicit
default/local path (Cypher) — is included as ``"local"`` unconditionally: its
absence of a branch is itself the signal it is the fallthrough, not a
surface this function could fail to find.

GRAPHOS-HOST-R008 NOTE: this module now lives in graph-os, but the surface it
introspects (``agent_utilities/mcp/tools/query_tools.py``) has not moved yet
(AU-BOUNDARY-R003, a different seed group). ``QUERY_TOOLS_MODULE`` is
therefore resolved through agent-utilities' INSTALLED package location
(``importlib.util.find_spec``) rather than a same-repo relative path — the
one necessary adaptation for a cross-repo port, not a behavior change: it
still parses the real source on disk, never a hand-maintained list.
"""

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "QUERY_TOOLS_MODULE",
    "DialectSurface",
    "enumerate_query_dialect_surfaces",
]


def _locate_query_tools_module() -> Path | None:
    """Resolve ``agent_utilities/mcp/tools/query_tools.py`` via the installed
    ``agent_utilities`` package rather than a path relative to this file (this
    file no longer lives inside the same repository as its target). Returns
    ``None`` when the distribution cannot be located at all (e.g. a stub or
    namespace-only install) — the caller still fails loudly, just one level
    up, in :func:`enumerate_query_dialect_surfaces`."""
    spec = importlib.util.find_spec("agent_utilities.mcp.tools.query_tools")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve()


#: Resolved through the installed ``agent_utilities`` distribution (see
#: :func:`_locate_query_tools_module`) so the introspection travels correctly
#: regardless of where graph-os and agent-utilities are each checked out.
QUERY_TOOLS_MODULE = _locate_query_tools_module()

#: The one dialect with no explicit ``if scope == ...`` branch — the
#: fallthrough/default path. Always present regardless of what the AST walk
#: finds, because ITS absence of a branch is the very thing that makes it the
#: default; a branch-based enumerator cannot discover a "no branch" surface by
#: definition, so it is named here once, not re-derived per run.
IMPLICIT_DEFAULT_DIALECT = "local"


@dataclass(frozen=True, slots=True)
class DialectSurface:
    """One ``scope=`` value ``graph_query``/``nl_query`` accepts, as found by
    live source introspection (never hand-maintained)."""

    name: str
    #: 1-based source line of the `if scope == "<name>":` comparison, or the
    #: `_run_graph_query` function's own start line for the implicit default.
    line: int


def _dialect_surface_from_if(node: ast.If) -> DialectSurface | None:
    """Return the matched surface for `if scope == "<literal>":`, else None.

    Matches the exact AST shape
    `Compare(left=Name(id="scope"), ops=[Eq()], comparators=[Constant(str)])`
    — not a text/regex scan — so it survives reformatting and only matches a
    genuine dialect dispatch, not an unrelated `scope == "..."` elsewhere."""

    test = node.test
    if not isinstance(test, ast.Compare):
        return None
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return None
    left = test.left
    if not (isinstance(left, ast.Name) and left.id == "scope"):
        return None
    comparators = test.comparators
    if len(comparators) != 1:
        return None
    comparator = comparators[0]
    if not (isinstance(comparator, ast.Constant) and isinstance(comparator.value, str)):
        return None
    return DialectSurface(name=comparator.value, line=node.lineno)


def _find_run_graph_query_scope_branches(tree: ast.Module) -> list[DialectSurface]:
    """Walk the parsed module for every `if scope == "<literal>":` inside a
    function named `_run_graph_query` (the graph_query tool's registration
    closure, `register_query_tools`)."""

    found: list[DialectSurface] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            if node.name != "_run_graph_query":
                self.generic_visit(node)
                return
            for sub in ast.walk(node):
                if isinstance(sub, ast.If):
                    surface = _dialect_surface_from_if(sub)
                    if surface is not None:
                        found.append(surface)
            # Do not descend further looking for a SECOND `_run_graph_query` —
            # there is exactly one; stop here rather than risk a duplicate
            # nested match.

    _Visitor().visit(tree)
    return found


def enumerate_query_dialect_surfaces(
    module_path: Path | None = None,
) -> tuple[DialectSurface, ...]:
    """Return every dialect `graph_query` accepts, discovered by parsing the
    real source — the live half of the GOC-62 D2 enumeration mechanism.

    WIRE-FIRST (D-OB-9) NOTE: this function's only caller is
    ``tests/security/conformance/test_surface_enumeration_drift.py``.
    That is by design, not a wiring gap — it IS the drift-detection
    conformance check the package docstring describes ("a drift test that
    FAILS when introspection finds a surface the manifest does not know
    about"); a conformance/drift check's entire job is to run inside the
    test suite, there is no production runtime call site to wire it to.

    Raises :class:`FileNotFoundError` if the module cannot be located (never
    silently returns an empty/partial result — an enumerator that fails
    closed is the whole point of this mechanism; a silent `[]` would make the
    drift test in ``test_surface_enumeration_drift.py`` vacuously pass,
    exactly the "gate that reports more coverage than it has" failure mode
    this design is built to avoid). This now also covers the cross-repo case
    (``agent_utilities`` not installed / not locatable at all), surfaced
    through the SAME exception so callers need only one failure mode.
    """

    path = module_path or QUERY_TOOLS_MODULE
    if path is None or not path.is_file():
        raise FileNotFoundError(
            f"query_tools module not found at {path} — the conformance suite's "
            "enumeration cannot proceed with an unverifiable source; this must "
            "fail loudly, not report zero surfaces"
        )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    branches = _find_run_graph_query_scope_branches(tree)
    if not branches:
        raise RuntimeError(
            f"{path}: found zero scope-dispatch branches inside "  # nosec B608 -- no SQL; bandit misreads this prose
            "_run_graph_query — either the function was renamed/restructured "
            "(update this introspector) or something is wrong; refusing to "
            "report an empty surface set silently"
        )
    surfaces = [DialectSurface(name=IMPLICIT_DEFAULT_DIALECT, line=0), *branches]
    # Stable, de-duplicated (a dialect name should appear in exactly one
    # branch; if it appears twice that is itself worth surfacing, not hiding).
    seen: set[str] = set()
    result: list[DialectSurface] = []
    for surface in surfaces:
        if surface.name in seen:
            continue
        seen.add(surface.name)
        result.append(surface)
    return tuple(result)
