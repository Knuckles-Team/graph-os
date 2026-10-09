"""Operational admin operations (GRAPHOS-OPS-R024.5, admin slice).

Implements the admin slice of ``GRAPHOS-OPS-R024``: ``ops.doctor``,
``ops.config``, ``ops.tenants``, ``ops.backup``, and ``ops.restore`` over
the hosted operational-admin store, bound by the caller's own authority and
scoped to the caller's own tenant, through one typed port, matching the
memory-store convention in :mod:`graph_os.api.ops.memory`. An uncomposed
store fails closed with a typed ``UNAVAILABLE`` refusal rather than
fabricating a result. Graph/query/search/ontology administration is out of
scope here; it is owned by ``GRAPHOS-HOST-R026.x``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    Verb,
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpsDoctorParams(_Params):
    pass


class OpsConfigParams(_Params):
    pass


class OpsTenantsParams(_Params):
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class OpsBackupParams(_Params):
    label: str = Field(min_length=1, max_length=256)


class OpsRestoreParams(_Params):
    backup_id: str = Field(min_length=1, max_length=256)


class DoctorReport(BaseModel):
    """One tenant-scoped diagnostic report; the store owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)


class OperationalConfig(BaseModel):
    """One tenant-scoped operational config snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    settings: dict[str, Any] = Field(default_factory=dict)


class TenantPage(BaseModel):
    """One cursor-paged slice of tenants visible to the caller's own authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenants: tuple[str, ...] = ()
    next_cursor: str | None = None


class BackupReceipt(BaseModel):
    """One durable backup receipt; the store owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    backup_id: str
    status: str


class RestoreReceipt(BaseModel):
    """One durable restore receipt; the store owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    backup_id: str
    status: str


class OpsDoctorResult(_Params):
    value: DoctorReport


class OpsConfigResult(_Params):
    value: OperationalConfig


class OpsTenantsResult(_Params):
    value: TenantPage


class OpsBackupResult(_Params):
    value: BackupReceipt


class OpsRestoreResult(_Params):
    value: RestoreReceipt


@runtime_checkable
class OpsAdminStore(Protocol):
    """The one typed port GraphOS calls the operational-admin store through."""

    async def doctor(self, *, tenant: str) -> DoctorReport: ...

    async def get_config(self, *, tenant: str) -> OperationalConfig: ...

    async def list_tenants(
        self, *, tenant: str, cursor: str | None, limit: int
    ) -> TenantPage: ...

    async def backup(
        self, *, tenant: str, label: str, idempotency_key: str
    ) -> BackupReceipt: ...

    async def restore(
        self, *, tenant: str, backup_id: str, idempotency_key: str
    ) -> RestoreReceipt: ...


def _bound_store(context: Any) -> OpsAdminStore:
    store = context.services.get("ops_admin_store")
    if store is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "operational-admin store is not composed"}
        )
    return store


async def handle_ops_doctor(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return one diagnostic report scoped to the caller's own tenant."""
    store = _bound_store(context)
    report = await store.doctor(tenant=context.caller.tenant)
    return {"value": report.model_dump(mode="json")}


async def handle_ops_config(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return the effective operational config scoped to the caller's own tenant."""
    store = _bound_store(context)
    config = await store.get_config(tenant=context.caller.tenant)
    return {"value": config.model_dump(mode="json")}


async def handle_ops_tenants(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return one cursor-paged slice of tenants scoped to the caller's own authority."""
    store = _bound_store(context)
    page = await store.list_tenants(
        tenant=context.caller.tenant,
        cursor=params.get("cursor"),
        limit=params["limit"],
    )
    return {"value": page.model_dump(mode="json")}


async def handle_ops_backup(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Reserve one durable backup scoped to the caller's own tenant."""
    if not context.idempotency_key:
        raise ValueError("Idempotency-Key is required")
    store = _bound_store(context)
    receipt = await store.backup(
        tenant=context.caller.tenant,
        label=params["label"],
        idempotency_key=context.idempotency_key,
    )
    return {"value": receipt.model_dump(mode="json")}


async def handle_ops_restore(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Restore one durable backup scoped to the caller's own tenant."""
    if not context.idempotency_key:
        raise ValueError("Idempotency-Key is required")
    store = _bound_store(context)
    receipt = await store.restore(
        tenant=context.caller.tenant,
        backup_id=params["backup_id"],
        idempotency_key=context.idempotency_key,
    )
    return {"value": receipt.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="ops.doctor",
            verb=Verb.ASK,
            summary="Run operational diagnostics scoped to the caller's own tenant",
            examples=("run a diagnostic check on this tenant",),
            params=OpsDoctorParams,
            result=OpsDoctorResult,
            binding=Composite(handler="graph_os.api.ops.ops.handle_ops_doctor"),
            scopes=frozenset({"ops:read"}),
            effect=Effect.READ,
        ),
        OpSpec(
            id="ops.config",
            verb=Verb.ASK,
            summary="Read the effective operational config for the caller's own tenant",
            examples=("show the current operational config",),
            params=OpsConfigParams,
            result=OpsConfigResult,
            binding=Composite(handler="graph_os.api.ops.ops.handle_ops_config"),
            scopes=frozenset({"ops:read"}),
            effect=Effect.READ,
        ),
        OpSpec(
            id="ops.tenants",
            verb=Verb.ASK,
            summary="List tenants visible under the caller's own admin authority",
            examples=("list the tenants I can administer",),
            params=OpsTenantsParams,
            result=OpsTenantsResult,
            binding=Composite(handler="graph_os.api.ops.ops.handle_ops_tenants"),
            scopes=frozenset({"ops:read"}),
            effect=Effect.READ,
        ),
        OpSpec(
            id="ops.backup",
            verb=Verb.ACT,
            summary="Reserve a durable backup scoped to the caller's own tenant",
            examples=("back up this tenant now",),
            params=OpsBackupParams,
            result=OpsBackupResult,
            binding=Composite(handler="graph_os.api.ops.ops.handle_ops_backup"),
            scopes=frozenset({"ops:admin"}),
            effect=Effect.ADMIN,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="ops.restore",
            verb=Verb.ACT,
            summary="Restore a durable backup scoped to the caller's own tenant",
            examples=("restore this tenant from a backup",),
            params=OpsRestoreParams,
            result=OpsRestoreResult,
            binding=Composite(handler="graph_os.api.ops.ops.handle_ops_restore"),
            scopes=frozenset({"ops:admin"}),
            effect=Effect.ADMIN,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
    )


specs = operations

__all__ = [
    "BackupReceipt",
    "DoctorReport",
    "OperationalConfig",
    "OpsAdminStore",
    "OpsBackupParams",
    "OpsBackupResult",
    "OpsConfigParams",
    "OpsConfigResult",
    "OpsDoctorParams",
    "OpsDoctorResult",
    "OpsRestoreParams",
    "OpsRestoreResult",
    "OpsTenantsParams",
    "OpsTenantsResult",
    "RestoreReceipt",
    "TenantPage",
    "handle_ops_backup",
    "handle_ops_config",
    "handle_ops_doctor",
    "handle_ops_restore",
    "handle_ops_tenants",
    "operations",
    "specs",
]
