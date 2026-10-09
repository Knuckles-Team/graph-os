"""Focused authority tests for the telemetry, security, and usage read ports.

Covers GRAPHOS-OPS-R024.1 (telemetry), GRAPHOS-OPS-R024.2 (security), and
GRAPHOS-OPS-R024.3 (usage): each is a single tenant-scoped read bound to one
typed port, matching the ingest-runner-facade convention
(GRAPHOS-OPS-R020.1/R020.2).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import security, telemetry, usage
from graph_os.api.ops.security import SecurityPosture
from graph_os.api.ops.telemetry import TelemetrySnapshot
from graph_os.api.ops.usage import UsageSnapshot


def _context(*, reader: object | None, service_name: str):
    caller = SimpleNamespace(tenant="tenant-a", principal="caller-a")
    services = {} if reader is None else {service_name: reader}
    return SimpleNamespace(caller=caller, services=services, idempotency_key=None)


class _FakeTelemetryReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, *, tenant: str) -> TelemetrySnapshot:
        self.calls.append(tenant)
        return TelemetrySnapshot(tenant=tenant, metrics={"requests": 1.0})


async def test_telemetry_read_returns_a_tenant_scoped_snapshot(op_by_id) -> None:
    reader = _FakeTelemetryReader()
    context = _context(reader=reader, service_name="telemetry_reader")
    op = op_by_id(telemetry.operations(), "telemetry.read")
    result = await telemetry.handle_telemetry_read(context, {}, op)
    assert result == {"value": {"tenant": "tenant-a", "metrics": {"requests": 1.0}}}
    assert reader.calls == ["tenant-a"]


async def test_telemetry_read_fails_closed_when_reader_not_composed(op_by_id) -> None:
    context = _context(reader=None, service_name="telemetry_reader")
    op = op_by_id(telemetry.operations(), "telemetry.read")
    with pytest.raises(OperationRefused) as excinfo:
        await telemetry.handle_telemetry_read(context, {}, op)
    assert excinfo.value.code == "UNAVAILABLE"


def test_telemetry_operation_declares_the_read_scope() -> None:
    (op,) = telemetry.operations()
    assert op.id == "telemetry.read"
    assert op.scopes == frozenset({"telemetry:read"})
    assert op.effect.value == "read"


class _FakeSecurityReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, *, tenant: str) -> SecurityPosture:
        self.calls.append(tenant)
        return SecurityPosture(tenant=tenant, findings=("clean",))


async def test_security_posture_read_returns_a_tenant_scoped_posture(
    op_by_id,
) -> None:
    reader = _FakeSecurityReader()
    context = _context(reader=reader, service_name="security_posture_reader")
    op = op_by_id(security.operations(), "security.posture.read")
    result = await security.handle_security_posture_read(context, {}, op)
    assert result == {"value": {"tenant": "tenant-a", "findings": ["clean"]}}
    assert reader.calls == ["tenant-a"]


async def test_security_posture_read_fails_closed_when_reader_not_composed(
    op_by_id,
) -> None:
    context = _context(reader=None, service_name="security_posture_reader")
    op = op_by_id(security.operations(), "security.posture.read")
    with pytest.raises(OperationRefused) as excinfo:
        await security.handle_security_posture_read(context, {}, op)
    assert excinfo.value.code == "UNAVAILABLE"


def test_security_operation_declares_the_read_scope() -> None:
    (op,) = security.operations()
    assert op.id == "security.posture.read"
    assert op.scopes == frozenset({"security:read"})
    assert op.effect.value == "read"


class _FakeUsageReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, *, tenant: str) -> UsageSnapshot:
        self.calls.append(tenant)
        return UsageSnapshot(tenant=tenant, totals={"tokens": 42.0})


async def test_usage_read_returns_a_tenant_scoped_snapshot(op_by_id) -> None:
    reader = _FakeUsageReader()
    context = _context(reader=reader, service_name="usage_reader")
    op = op_by_id(usage.operations(), "usage.read")
    result = await usage.handle_usage_read(context, {}, op)
    assert result == {"value": {"tenant": "tenant-a", "totals": {"tokens": 42.0}}}
    assert reader.calls == ["tenant-a"]


async def test_usage_read_fails_closed_when_reader_not_composed(op_by_id) -> None:
    context = _context(reader=None, service_name="usage_reader")
    op = op_by_id(usage.operations(), "usage.read")
    with pytest.raises(OperationRefused) as excinfo:
        await usage.handle_usage_read(context, {}, op)
    assert excinfo.value.code == "UNAVAILABLE"


def test_usage_operation_declares_the_read_scope() -> None:
    (op,) = usage.operations()
    assert op.id == "usage.read"
    assert op.scopes == frozenset({"usage:read"})
    assert op.effect.value == "read"
