"""Typed publication gate for the durable four-family MCP resource/template set.

GRAPHOS-MCP-RESOURCES-001 (R001.1): graph-os does not maintain a second,
process-local durability store for MCP tools/prompts/resources/resource
templates. It only claims publication once epistemic-graph hands back a
``ReconciliationReceipt`` acknowledging the exact candidate generation and
digest for all four families. Until that receipt exists, is stale, or does
not match the candidate, the gate returns the typed ``reingestion-unreconciled``
result documented in ``docs/status.md`` / ``docs/fleet.md`` — graph-os never
claims convergence it cannot prove.

See ``specs/mcp-resource-publication-reconciliation/spec.md``; the
reconciliation receipt itself is produced on the epistemic-graph side per
``EG-REPO-INGEST-001`` (``specs/repository-index-and-ingestion`` in
epistemic-graph).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The durable four-family MCP resource/template set this gate reconciles.
MCP_RESOURCE_FAMILIES: frozenset[str] = frozenset(
    {"tools", "prompts", "resources", "resource_templates"}
)

GateStatus = Literal["reconciled", "reingestion-unreconciled"]

#: Stable sub-reasons for a ``reingestion-unreconciled`` result.
UnreconciledReason = Literal["missing", "stale", "invalid"]


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    """The durable acknowledgement epistemic-graph hands back for one candidate.

    ``acknowledged_families`` must cover exactly ``MCP_RESOURCE_FAMILIES``; a
    receipt that only covers a subset cannot certify the whole four-family set.
    """

    tenant_id: str
    catalog_generation: int
    snapshot_digest: str
    acknowledged_families: frozenset[str]
    issued_at_ms: int


@dataclass(frozen=True, slots=True)
class PublicationGateResult:
    """The typed outcome of evaluating a candidate publication against a receipt."""

    status: GateStatus
    reason: UnreconciledReason | None = None
    receipt: ReconciliationReceipt | None = None

    @property
    def code(self) -> str:
        """The stable machine-readable status code, as published in status docs."""
        return self.status


def _reconciled(receipt: ReconciliationReceipt) -> PublicationGateResult:
    return PublicationGateResult(status="reconciled", reason=None, receipt=receipt)


def _unreconciled(
    reason: UnreconciledReason, receipt: ReconciliationReceipt | None
) -> PublicationGateResult:
    return PublicationGateResult(
        status="reingestion-unreconciled", reason=reason, receipt=receipt
    )


def gate_mcp_resource_publication(
    receipt: ReconciliationReceipt | None,
    *,
    tenant_id: str,
    expected_generation: int,
    expected_digest: str,
    now_ms: int,
    max_age_ms: int,
) -> PublicationGateResult:
    """Decide whether graph-os may claim publication of a candidate generation.

    Returns a ``reconciled`` result only when a receipt exists, matches the
    candidate's exact tenant, ``catalog_generation``, and ``snapshot_digest``,
    acknowledges every family in ``MCP_RESOURCE_FAMILIES``, and is not older
    than ``max_age_ms``. Any other case returns the typed
    ``reingestion-unreconciled`` result with a stable ``reason`` instead of
    raising, so a caller can report it without claiming convergence.
    """
    if receipt is None:
        return _unreconciled("missing", None)

    if (
        receipt.tenant_id != tenant_id
        or receipt.catalog_generation != expected_generation
        or receipt.snapshot_digest != expected_digest
        or receipt.acknowledged_families != MCP_RESOURCE_FAMILIES
    ):
        return _unreconciled("invalid", receipt)

    age_ms = now_ms - receipt.issued_at_ms
    if age_ms < 0 or age_ms > max_age_ms:
        return _unreconciled("stale", receipt)

    return _reconciled(receipt)


class PublicationRefusedError(Exception):
    """Raised when a candidate generation may not be swapped in as published.

    Carries the typed ``PublicationGateResult`` so the caller can surface the
    stable ``reason`` instead of swallowing the refusal.
    """

    def __init__(self, result: PublicationGateResult) -> None:
        self.result = result
        super().__init__(f"{result.code}: publication refused (reason={result.reason})")


def require_reconciled_for_swap(
    receipt: ReconciliationReceipt | None,
    *,
    tenant_id: str,
    expected_generation: int,
    expected_digest: str,
    now_ms: int,
    max_age_ms: int,
) -> ReconciliationReceipt:
    """Return the matching receipt, or raise ``PublicationRefusedError``.

    Wraps ``gate_mcp_resource_publication`` so the publish step cannot bypass
    the gate by ignoring a returned status.
    """
    result = gate_mcp_resource_publication(
        receipt,
        tenant_id=tenant_id,
        expected_generation=expected_generation,
        expected_digest=expected_digest,
        now_ms=now_ms,
        max_age_ms=max_age_ms,
    )
    if result.status != "reconciled" or result.receipt is None:
        raise PublicationRefusedError(result)
    return result.receipt
