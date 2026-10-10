"""Tests for the typed MCP resource/template publication gate.

Covers GRAPHOS-MCP-RESOURCES-001 R001.1: missing, stale, and invalid receipts
all return the typed ``reingestion-unreconciled`` result instead of claiming
publication, and a receipt matching the exact candidate generation/digest and
acknowledging all four families is reconciled.
"""

from __future__ import annotations

import pytest

from graph_os.fleet.mcp_resource_reconciliation import (
    MCP_RESOURCE_FAMILIES,
    PublicationGateResult,
    ReconciliationReceipt,
    gate_mcp_resource_publication,
)

pytestmark = pytest.mark.spec("GRAPHOS-MCP-RESOURCES-R001.1")

TENANT = "tenant-a"
GENERATION = 7
DIGEST = "sha256:abc123"
NOW_MS = 1_000_000
MAX_AGE_MS = 60_000


def _valid_receipt(**overrides: object) -> ReconciliationReceipt:
    fields: dict[str, object] = {
        "tenant_id": TENANT,
        "catalog_generation": GENERATION,
        "snapshot_digest": DIGEST,
        "acknowledged_families": MCP_RESOURCE_FAMILIES,
        "issued_at_ms": NOW_MS - 1_000,
    }
    fields.update(overrides)
    return ReconciliationReceipt(**fields)  # type: ignore[arg-type]


def _gate(receipt: ReconciliationReceipt | None) -> PublicationGateResult:
    return gate_mcp_resource_publication(
        receipt,
        tenant_id=TENANT,
        expected_generation=GENERATION,
        expected_digest=DIGEST,
        now_ms=NOW_MS,
        max_age_ms=MAX_AGE_MS,
    )


def test_missing_receipt_is_reingestion_unreconciled() -> None:
    result = _gate(None)
    assert result.status == "reingestion-unreconciled"
    assert result.code == "reingestion-unreconciled"
    assert result.reason == "missing"
    assert result.receipt is None


def test_stale_receipt_is_reingestion_unreconciled() -> None:
    stale = _valid_receipt(issued_at_ms=NOW_MS - MAX_AGE_MS - 1)
    result = _gate(stale)
    assert result.status == "reingestion-unreconciled"
    assert result.reason == "stale"
    assert result.receipt is stale


def test_digest_mismatch_receipt_is_reingestion_unreconciled() -> None:
    mismatched = _valid_receipt(snapshot_digest="sha256:other")
    result = _gate(mismatched)
    assert result.status == "reingestion-unreconciled"
    assert result.reason == "invalid"


def test_generation_mismatch_receipt_is_reingestion_unreconciled() -> None:
    mismatched = _valid_receipt(catalog_generation=GENERATION + 1)
    result = _gate(mismatched)
    assert result.status == "reingestion-unreconciled"
    assert result.reason == "invalid"


def test_partial_family_acknowledgement_is_reingestion_unreconciled() -> None:
    partial = _valid_receipt(
        acknowledged_families=frozenset({"tools", "prompts"}),
    )
    result = _gate(partial)
    assert result.status == "reingestion-unreconciled"
    assert result.reason == "invalid"


def test_wrong_tenant_receipt_is_reingestion_unreconciled() -> None:
    wrong_tenant = _valid_receipt(tenant_id="tenant-b")
    result = _gate(wrong_tenant)
    assert result.status == "reingestion-unreconciled"
    assert result.reason == "invalid"


def test_valid_receipt_is_reconciled() -> None:
    receipt = _valid_receipt()
    result = _gate(receipt)
    assert result.status == "reconciled"
    assert result.reason is None
    assert result.receipt is receipt
