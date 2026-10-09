"""Focused authority tests for the ingest runner facade (GRAPHOS-OPS-R020.1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops.ingest import operations, sync_source
from graph_os.ingest.service import IngestSyncMode, IngestSyncReceipt


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def sync_source(
        self, *, tenant: str, source_id: str, mode: IngestSyncMode, idempotency_key: str
    ) -> IngestSyncReceipt:
        self.calls.append((tenant, source_id, mode, idempotency_key))
        return IngestSyncReceipt(
            job_id="job-1",
            source_id=source_id,
            tenant=tenant,
            mode=mode,
            status="queued",
        )


def _context(*, runner: object | None, idempotency_key: str | None = "idem-1"):
    caller = SimpleNamespace(tenant="tenant-a", principal="caller-a")
    services = {} if runner is None else {"ingest_runner": runner}
    return SimpleNamespace(
        caller=caller, services=services, idempotency_key=idempotency_key
    )


async def test_sync_returns_a_durable_job_receipt() -> None:
    runner = _FakeRunner()
    context = _context(runner=runner)
    result = await sync_source(
        context, {"source_id": "src-1", "mode": "incremental"}, None
    )
    assert result == {
        "value": {
            "job_id": "job-1",
            "source_id": "src-1",
            "tenant": "tenant-a",
            "mode": "incremental",
            "status": "queued",
        }
    }
    assert runner.calls == [("tenant-a", "src-1", "incremental", "idem-1")]


async def test_sync_fails_closed_with_unavailable_when_runner_not_composed() -> None:
    context = _context(runner=None)
    with pytest.raises(OperationRefused) as excinfo:
        await sync_source(context, {"source_id": "src-1", "mode": "incremental"}, None)
    assert excinfo.value.code == "UNAVAILABLE"


async def test_sync_requires_an_idempotency_key() -> None:
    context = _context(runner=_FakeRunner(), idempotency_key=None)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await sync_source(context, {"source_id": "src-1", "mode": "incremental"}, None)


def test_operation_declares_the_write_scope_and_audit_class() -> None:
    ops = {op.id: op for op in operations()}
    op = ops["ingest.sources.sync"]
    assert op.scopes == frozenset({"ingest:write"})
    assert op.audit.value == "event"
    assert op.idempotency.value == "key_required"
