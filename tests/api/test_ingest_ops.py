"""Focused authority tests for the ingest runner facade.

Covers GRAPHOS-OPS-R020.1 (source sync) and GRAPHOS-OPS-R020.2 (repository
indexing, source list/status).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops.ingest import (
    get_source_status,
    index_repository,
    list_sources,
    operations,
    sync_source,
)
from graph_os.ingest.service import (
    IngestIndexReceipt,
    IngestSourceInventory,
    IngestSourceRecord,
    IngestSyncMode,
    IngestSyncReceipt,
)


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        self.index_calls: list[tuple[str, str, str]] = []
        self.list_calls: list[str] = []
        self.status_calls: list[tuple[str, str]] = []

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

    async def index_repository(
        self, *, tenant: str, repo_id: str, idempotency_key: str
    ) -> IngestIndexReceipt:
        self.index_calls.append((tenant, repo_id, idempotency_key))
        return IngestIndexReceipt(
            job_id="job-2", repo_id=repo_id, tenant=tenant, status="queued"
        )

    async def list_sources(self, *, tenant: str) -> IngestSourceInventory:
        self.list_calls.append(tenant)
        return IngestSourceInventory(
            sources=(
                IngestSourceRecord(source_id="src-1", tenant=tenant, state="active"),
            )
        )

    async def get_source_status(
        self, *, tenant: str, source_id: str
    ) -> IngestSourceRecord:
        self.status_calls.append((tenant, source_id))
        return IngestSourceRecord(source_id=source_id, tenant=tenant, state="active")


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


@pytest.mark.spec(
    "GRAPHOS-OPS-R020.1",
    "GRAPHOS-OPS-R020.2",
    "GRAPHOS-OPS-R020.3",
    "GRAPHOS-OPS-R020.4",
    "GRAPHOS-OPS-R020.5",
)
def test_operation_declares_the_write_scope_and_audit_class() -> None:
    ops = {op.id: op for op in operations()}
    op = ops["ingest.sources.sync"]
    assert op.scopes == frozenset({"ingest:write"})
    assert op.audit.value == "event"
    assert op.idempotency.value == "key_required"


@pytest.mark.spec(
    "GRAPHOS-OPS-R020.1",
    "GRAPHOS-OPS-R020.2",
    "GRAPHOS-OPS-R020.3",
    "GRAPHOS-OPS-R020.4",
    "GRAPHOS-OPS-R020.5",
)
async def test_index_returns_a_durable_job_receipt() -> None:
    runner = _FakeRunner()
    context = _context(runner=runner)
    result = await index_repository(context, {"repo_id": "repo-1"}, None)
    assert result == {
        "value": {
            "job_id": "job-2",
            "repo_id": "repo-1",
            "tenant": "tenant-a",
            "status": "queued",
        }
    }
    assert runner.index_calls == [("tenant-a", "repo-1", "idem-1")]


@pytest.mark.spec(
    "GRAPHOS-OPS-R020.1",
    "GRAPHOS-OPS-R020.2",
    "GRAPHOS-OPS-R020.3",
    "GRAPHOS-OPS-R020.4",
    "GRAPHOS-OPS-R020.5",
)
async def test_index_fails_closed_with_unavailable_when_runner_not_composed() -> None:
    context = _context(runner=None)
    with pytest.raises(OperationRefused) as excinfo:
        await index_repository(context, {"repo_id": "repo-1"}, None)
    assert excinfo.value.code == "UNAVAILABLE"


async def test_index_requires_an_idempotency_key() -> None:
    context = _context(runner=_FakeRunner(), idempotency_key=None)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await index_repository(context, {"repo_id": "repo-1"}, None)


async def test_list_sources_returns_the_tenant_inventory() -> None:
    runner = _FakeRunner()
    context = _context(runner=runner)
    result = await list_sources(context, {}, None)
    assert result == {
        "value": {
            "sources": [{"source_id": "src-1", "tenant": "tenant-a", "state": "active"}]
        }
    }
    assert runner.list_calls == ["tenant-a"]


async def test_list_sources_fails_closed_with_unavailable_when_runner_not_composed() -> (
    None
):
    context = _context(runner=None)
    with pytest.raises(OperationRefused) as excinfo:
        await list_sources(context, {}, None)
    assert excinfo.value.code == "UNAVAILABLE"


async def test_source_status_returns_the_current_record() -> None:
    runner = _FakeRunner()
    context = _context(runner=runner)
    result = await get_source_status(context, {"source_id": "src-1"}, None)
    assert result == {
        "value": {"source_id": "src-1", "tenant": "tenant-a", "state": "active"}
    }
    assert runner.status_calls == [("tenant-a", "src-1")]


async def test_source_status_fails_closed_with_unavailable_when_runner_not_composed() -> (
    None
):
    context = _context(runner=None)
    with pytest.raises(OperationRefused) as excinfo:
        await get_source_status(context, {"source_id": "src-1"}, None)
    assert excinfo.value.code == "UNAVAILABLE"


def test_index_operation_declares_the_write_scope_and_audit_class() -> None:
    ops = {op.id: op for op in operations()}
    op = ops["ingest.repositories.index"]
    assert op.scopes == frozenset({"ingest:write"})
    assert op.audit.value == "event"
    assert op.idempotency.value == "key_required"


def test_source_inventory_operations_declare_the_read_scope() -> None:
    ops = {op.id: op for op in operations()}
    for op_id in ("ingest.sources.list", "ingest.sources.status"):
        op = ops[op_id]
        assert op.scopes == frozenset({"ingest:read"})
        assert op.effect.value == "read"
