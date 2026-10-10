"""Exit-criteria matrix registry and audit (GRAPHOS-RELEASE-R003.1).

GRAPHOS-RELEASE-R003 requires an exit-criteria matrix mapping each
release-readiness obligation to an executable test, and a test-suite audit
confirming every row resolves to a *passing* automated test. The seven
obligation rows named in ``specs/dependency-ordered-release/requirements.md``
span multiple repositories (ingestion receipts and SPARQL/natural-language
query proofs live in epistemic-graph; the multi-agent orchestration graph in
agent-utilities; connector certification in agent-connector-sdk; and so on),
so wiring every row to a currently-passing test is cross-repo work tracked
separately as ``GRAPHOS-RELEASE-R003.2`` and is not claimed here.

This module delivers the graph-os side available against the currently
pinned epistemic-graph: the typed matrix registry and the audit function a
test-suite audit runs against pytest's own collected node IDs. Rows are
registered by callers (this module ships no fabricated rows); an unregistered
obligation audits as missing, never as a silent pass.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict


class ExitCriterion(BaseModel):
    """One exit-criteria matrix row: a readiness obligation bound to a test."""

    model_config = ConfigDict(frozen=True)

    obligation_id: str
    description: str
    test_node_id: str


class ExitCriteriaAudit(BaseModel):
    """Per-row audit outcome for a matrix against a set of collected test IDs."""

    model_config = ConfigDict(frozen=True)

    satisfied: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def all_satisfied(self) -> bool:
        return not self.missing


def audit_exit_criteria(
    matrix: Iterable[ExitCriterion],
    collected_test_ids: Iterable[str],
) -> ExitCriteriaAudit:
    """Report which matrix rows resolve to a test present in ``collected_test_ids``.

    ``collected_test_ids`` is the passing-test universe a test-suite audit
    observed (for example pytest's collected/passed node IDs); a row whose
    ``test_node_id`` is absent from that set audits as missing, never as a
    best-effort pass.
    """
    available = set(collected_test_ids)
    satisfied: list[str] = []
    missing: list[str] = []
    for criterion in matrix:
        if criterion.test_node_id in available:
            satisfied.append(criterion.obligation_id)
        else:
            missing.append(criterion.obligation_id)
    return ExitCriteriaAudit(satisfied=tuple(satisfied), missing=tuple(missing))
