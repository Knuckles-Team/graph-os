"""Focused authority tests for the operational-admin store (GRAPHOS-OPS-R024.5).

Covers ops.doctor, ops.config, ops.tenants, ops.backup, and ops.restore: all
bound to one typed port, scoped to the caller's own tenant, failing closed
with UNAVAILABLE when the port is not composed, matching the
memory-store convention (GRAPHOS-OPS-R024.4).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import ops as ops_admin
from graph_os.api.ops.ops import (
    BackupReceipt,
    DoctorReport,
    OperationalConfig,
    RestoreReceipt,
    TenantPage,
)

_OPS = {op.id: op for op in ops_admin.operations()}


def _context(
    *,
    store: object | None,
    idempotency_key: str | None = "idem-1",
    tenant: str = "tenant-a",
):
    caller = SimpleNamespace(tenant=tenant, principal="caller-a")
    services = {} if store is None else {"ops_admin_store": store}
    return SimpleNamespace(
        caller=caller, services=services, idempotency_key=idempotency_key
    )


class _FakeOpsAdminStore:
    def __init__(self) -> None:
        self.doctor_calls: list[str] = []
        self.config_calls: list[str] = []
        self.tenants_calls: list[tuple[str, str | None, int]] = []
        self.backup_calls: list[tuple[str, str, str]] = []
        self.restore_calls: list[tuple[str, str, str]] = []

    async def doctor(self, *, tenant: str) -> DoctorReport:
        self.doctor_calls.append(tenant)
        return DoctorReport(tenant=tenant, status="healthy", checks={"disk": "ok"})

    async def get_config(self, *, tenant: str) -> OperationalConfig:
        self.config_calls.append(tenant)
        return OperationalConfig(tenant=tenant, settings={"retention_days": 30})

    async def list_tenants(
        self, *, tenant: str, cursor: str | None, limit: int
    ) -> TenantPage:
        self.tenants_calls.append((tenant, cursor, limit))
        return TenantPage(tenants=(tenant,), next_cursor=None)

    async def backup(
        self, *, tenant: str, label: str, idempotency_key: str
    ) -> BackupReceipt:
        self.backup_calls.append((tenant, label, idempotency_key))
        return BackupReceipt(tenant=tenant, backup_id="backup-1", status="reserved")

    async def restore(
        self, *, tenant: str, backup_id: str, idempotency_key: str
    ) -> RestoreReceipt:
        self.restore_calls.append((tenant, backup_id, idempotency_key))
        return RestoreReceipt(tenant=tenant, backup_id=backup_id, status="reserved")


async def test_ops_doctor_is_scoped_to_the_callers_own_tenant() -> None:
    store = _FakeOpsAdminStore()
    context = _context(store=store, tenant="tenant-a")
    result = await ops_admin.handle_ops_doctor(context, {}, _OPS["ops.doctor"])
    assert result == {
        "value": {"tenant": "tenant-a", "status": "healthy", "checks": {"disk": "ok"}}
    }
    assert store.doctor_calls == ["tenant-a"]


async def test_ops_doctor_fails_closed_when_store_not_composed() -> None:
    context = _context(store=None)
    with pytest.raises(OperationRefused) as excinfo:
        await ops_admin.handle_ops_doctor(context, {}, _OPS["ops.doctor"])
    assert excinfo.value.code == "UNAVAILABLE"


async def test_ops_config_is_scoped_to_the_callers_own_tenant() -> None:
    store = _FakeOpsAdminStore()
    context = _context(store=store, tenant="tenant-b")
    result = await ops_admin.handle_ops_config(context, {}, _OPS["ops.config"])
    assert result == {
        "value": {"tenant": "tenant-b", "settings": {"retention_days": 30}}
    }
    assert store.config_calls == ["tenant-b"]


async def test_ops_config_fails_closed_when_store_not_composed() -> None:
    context = _context(store=None)
    with pytest.raises(OperationRefused) as excinfo:
        await ops_admin.handle_ops_config(context, {}, _OPS["ops.config"])
    assert excinfo.value.code == "UNAVAILABLE"


async def test_ops_tenants_is_scoped_to_the_callers_own_tenant() -> None:
    store = _FakeOpsAdminStore()
    context = _context(store=store, tenant="tenant-a")
    result = await ops_admin.handle_ops_tenants(
        context, {"cursor": None, "limit": 25}, _OPS["ops.tenants"]
    )
    assert result == {"value": {"tenants": ["tenant-a"], "next_cursor": None}}
    assert store.tenants_calls == [("tenant-a", None, 25)]


async def test_ops_tenants_fails_closed_when_store_not_composed() -> None:
    context = _context(store=None)
    with pytest.raises(OperationRefused) as excinfo:
        await ops_admin.handle_ops_tenants(
            context, {"cursor": None, "limit": 25}, _OPS["ops.tenants"]
        )
    assert excinfo.value.code == "UNAVAILABLE"


async def test_ops_backup_is_scoped_to_the_callers_own_tenant() -> None:
    store = _FakeOpsAdminStore()
    context = _context(store=store, tenant="tenant-b")
    result = await ops_admin.handle_ops_backup(
        context, {"label": "nightly"}, _OPS["ops.backup"]
    )
    assert result == {
        "value": {"tenant": "tenant-b", "backup_id": "backup-1", "status": "reserved"}
    }
    assert store.backup_calls == [("tenant-b", "nightly", "idem-1")]


async def test_ops_backup_fails_closed_when_store_not_composed() -> None:
    context = _context(store=None)
    with pytest.raises(OperationRefused) as excinfo:
        await ops_admin.handle_ops_backup(
            context, {"label": "nightly"}, _OPS["ops.backup"]
        )
    assert excinfo.value.code == "UNAVAILABLE"


async def test_ops_backup_requires_an_idempotency_key() -> None:
    context = _context(store=_FakeOpsAdminStore(), idempotency_key=None)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await ops_admin.handle_ops_backup(
            context, {"label": "nightly"}, _OPS["ops.backup"]
        )


async def test_ops_restore_is_scoped_to_the_callers_own_tenant() -> None:
    store = _FakeOpsAdminStore()
    context = _context(store=store, tenant="tenant-b")
    result = await ops_admin.handle_ops_restore(
        context, {"backup_id": "backup-1"}, _OPS["ops.restore"]
    )
    assert result == {
        "value": {"tenant": "tenant-b", "backup_id": "backup-1", "status": "reserved"}
    }
    assert store.restore_calls == [("tenant-b", "backup-1", "idem-1")]


async def test_ops_restore_fails_closed_when_store_not_composed() -> None:
    context = _context(store=None)
    with pytest.raises(OperationRefused) as excinfo:
        await ops_admin.handle_ops_restore(
            context, {"backup_id": "backup-1"}, _OPS["ops.restore"]
        )
    assert excinfo.value.code == "UNAVAILABLE"


async def test_ops_restore_requires_an_idempotency_key() -> None:
    context = _context(store=_FakeOpsAdminStore(), idempotency_key=None)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await ops_admin.handle_ops_restore(
            context, {"backup_id": "backup-1"}, _OPS["ops.restore"]
        )


def test_ops_admin_operations_declare_distinct_read_and_admin_scopes() -> None:
    operations = {op.id: op for op in ops_admin.operations()}
    assert operations["ops.doctor"].scopes == frozenset({"ops:read"})
    assert operations["ops.config"].scopes == frozenset({"ops:read"})
    assert operations["ops.tenants"].scopes == frozenset({"ops:read"})
    backup_op = operations["ops.backup"]
    restore_op = operations["ops.restore"]
    assert backup_op.scopes == frozenset({"ops:admin"})
    assert backup_op.effect.value == "admin"
    assert backup_op.idempotency.value == "key_required"
    assert restore_op.scopes == frozenset({"ops:admin"})
    assert restore_op.effect.value == "admin"
    assert restore_op.idempotency.value == "key_required"
