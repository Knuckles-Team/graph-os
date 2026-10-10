"""Focused authority tests for the ingest runner facade.

Covers GRAPHOS-OPS-R020.1 (source sync) and GRAPHOS-OPS-R020.2 (repository
indexing, source list/status).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops.ingest import (
    get_job_status,
    get_source_status,
    index_repository,
    list_packs,
    list_sources,
    operations,
    sync_source,
)
from graph_os.ingest.service import (
    IngestIndexReceipt,
    IngestJobRecord,
    IngestPackInventory,
    IngestPackRecord,
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
        self.pack_calls: list[str] = []
        self.job_calls: list[tuple[str, str]] = []

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

    async def list_packs(self, *, tenant: str) -> IngestPackInventory:
        self.pack_calls.append(tenant)
        return IngestPackInventory(
            packs=(IngestPackRecord(pack_id="pack-1", tenant=tenant, state="active"),)
        )

    async def get_job_status(self, *, tenant: str, job_id: str) -> IngestJobRecord:
        self.job_calls.append((tenant, job_id))
        return IngestJobRecord(job_id=job_id, tenant=tenant, status="running")


def _context(*, runner: object | None, idempotency_key: str | None = "idem-1"):
    caller = SimpleNamespace(tenant="tenant-a", principal="caller-a")
    services = {} if runner is None else {"ingest_runner": runner}
    return SimpleNamespace(
        caller=caller, services=services, idempotency_key=idempotency_key
    )


@pytest.mark.spec("GRAPHOS-OPS-R020.1")
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


@pytest.mark.spec("GRAPHOS-OPS-R020.1")
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


@pytest.mark.spec("GRAPHOS-OPS-R020.2")
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


@pytest.mark.spec("GRAPHOS-OPS-R020.2")
async def test_index_fails_closed_with_unavailable_when_runner_not_composed() -> None:
    context = _context(runner=None)
    with pytest.raises(OperationRefused) as excinfo:
        await index_repository(context, {"repo_id": "repo-1"}, None)
    assert excinfo.value.code == "UNAVAILABLE"


async def test_index_requires_an_idempotency_key() -> None:
    context = _context(runner=_FakeRunner(), idempotency_key=None)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await index_repository(context, {"repo_id": "repo-1"}, None)


@pytest.mark.spec("GRAPHOS-OPS-R020.2")
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


@pytest.mark.spec("GRAPHOS-OPS-R020.2")
async def test_list_sources_fails_closed_with_unavailable_when_runner_not_composed() -> (
    None
):
    context = _context(runner=None)
    with pytest.raises(OperationRefused) as excinfo:
        await list_sources(context, {}, None)
    assert excinfo.value.code == "UNAVAILABLE"


@pytest.mark.spec("GRAPHOS-OPS-R020.2")
async def test_source_status_returns_the_current_record() -> None:
    runner = _FakeRunner()
    context = _context(runner=runner)
    result = await get_source_status(context, {"source_id": "src-1"}, None)
    assert result == {
        "value": {"source_id": "src-1", "tenant": "tenant-a", "state": "active"}
    }
    assert runner.status_calls == [("tenant-a", "src-1")]


@pytest.mark.spec("GRAPHOS-OPS-R020.2")
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


@pytest.mark.spec("GRAPHOS-OPS-R020.3.1.1")
async def test_list_packs_returns_the_tenant_inventory() -> None:
    runner = _FakeRunner()
    result = await list_packs(_context(runner=runner), {}, None)
    assert result == {
        "value": {
            "packs": [{"pack_id": "pack-1", "tenant": "tenant-a", "state": "active"}]
        }
    }
    assert runner.pack_calls == ["tenant-a"]


@pytest.mark.spec("GRAPHOS-OPS-R020.3.1.1")
async def test_list_packs_fails_closed_with_unavailable_when_runner_not_composed() -> (
    None
):
    with pytest.raises(OperationRefused) as excinfo:
        await list_packs(_context(runner=None), {}, None)
    assert excinfo.value.code == "UNAVAILABLE"


@pytest.mark.spec("GRAPHOS-OPS-R020.3.1.1")
def test_packs_list_operation_declares_the_read_scope() -> None:
    op = {op.id: op for op in operations()}["ingest.packs.list"]
    assert op.scopes == frozenset({"ingest:read"})
    assert op.effect.value == "read"


@pytest.mark.spec("GRAPHOS-OPS-R020.3.1.2")
async def test_job_status_reports_durable_job_state() -> None:
    runner = _FakeRunner()
    result = await get_job_status(_context(runner=runner), {"job_id": "job-9"}, None)
    assert result == {
        "value": {"job_id": "job-9", "tenant": "tenant-a", "status": "running"}
    }
    assert runner.job_calls == [("tenant-a", "job-9")]


@pytest.mark.spec("GRAPHOS-OPS-R020.3.1.2")
async def test_job_status_fails_closed_with_unavailable_when_runner_not_composed() -> (
    None
):
    with pytest.raises(OperationRefused) as excinfo:
        await get_job_status(_context(runner=None), {"job_id": "job-9"}, None)
    assert excinfo.value.code == "UNAVAILABLE"


@pytest.mark.spec("GRAPHOS-OPS-R020.3.1.2")
def test_jobs_status_operation_declares_the_read_scope() -> None:
    op = {op.id: op for op in operations()}["ingest.jobs.status"]
    assert op.scopes == frozenset({"ingest:read"})
    assert op.effect.value == "read"
