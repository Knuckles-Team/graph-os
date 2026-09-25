"""Caller-bound durable child journal does not accept service or forged owner rows."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from graph_os.fleet.eg_child_journal import EgServiceChildJournal
from graph_os.fleet.service_child import ServiceChildRecord


def _caller():
    return SimpleNamespace(
        principal="alice",
        tenant="tenant-a",
        engine_claims={"principal": "alice"},
        effective_scopes=frozenset({"mcp:delegate", "security:audit-write"}),
    )


def _record():
    return ServiceChildRecord(
        record_id="0" * 64,
        owner_ref="principal:sha256:" + hashlib.sha256(b"alice").hexdigest(),
        principal="alice",
        tenant="tenant-a",
        server="s",
        tool="run",
        subject_id="component:1",
        argument_digest="1" * 64,
        request_id="request-1",
        audit_params_sha256="2" * 64,
        policy_revision="policy-1",
        registry_revision="3" * 64,
        scopes_sha256="4" * 64,
    )


@pytest.mark.asyncio
async def test_caller_context_binds_reserve_get_and_finish() -> None:
    events = []
    owner_ref = _record().owner_ref

    class Graph:
        async def service_child_reserve(self, binding, *, audit_ref):
            events.append(("reserve", binding, audit_ref))
            return {
                "tenant": "tenant-a",
                "record_id": "b" * 64,
                "owner_ref": owner_ref,
                "target": "fleet:s/run",
                "subject_id": "component:1",
                "audit_ref": audit_ref,
                "recovery_ref": "recovery:1",
                "durable": True,
                "created": True,
                "state": "reserved",
            }

        async def service_child_get(self, record_id):
            events.append(("get", record_id))
            return {
                "tenant": "tenant-a",
                "owner_ref": owner_ref,
                "record_id": record_id,
                "state": "reserved",
            }

        async def service_child_finish(self, record_id, **kwargs):
            events.append(("finish", record_id, kwargs))
            return {
                "tenant": "tenant-a",
                "owner_ref": owner_ref,
                "record_id": record_id,
                "state": kwargs["outcome"],
            }

    class Client:
        graph = Graph()

        @contextmanager
        def use_verified_context(self, claims):
            assert claims == {"principal": "alice"}
            events.append(("context", "enter"))
            yield
            events.append(("context", "exit"))

    async def client_factory(tenant):
        assert tenant == "tenant-a"
        return Client()

    journal = EgServiceChildJournal(client_factory)
    receipt = await journal.reserve(_record(), _caller())
    assert receipt.record_id == "b" * 64
    assert events[1][1]["audit_params_sha256"] == "2" * 64
    assert events[1][2] == "request-1"
    assert (await journal.get(receipt.record_id, _caller()))["state"] == "reserved"
    assert await journal.succeeded(receipt.record_id, "5" * 64, _caller())
    assert events[-2][2] == {
        "outcome": "succeeded",
        "result_sha256": "5" * 64,
        "reason_code": None,
    }


@pytest.mark.asyncio
async def test_receipt_from_other_tenant_or_owner_is_rejected() -> None:
    class Graph:
        async def service_child_reserve(self, _binding, *, audit_ref):
            return {
                "tenant": "tenant-b",
                "owner_ref": _record().owner_ref,
                "record_id": "b" * 64,
            }

    class Client:
        graph = Graph()

        @contextmanager
        def use_verified_context(self, _claims):
            yield

    async def client_factory(_tenant):
        return Client()

    with pytest.raises(RuntimeError, match="receipt identity"):
        await EgServiceChildJournal(client_factory).reserve(_record(), _caller())

    caller = _caller()
    caller.effective_scopes = frozenset({"mcp:delegate"})
    with pytest.raises(PermissionError, match="scopes"):
        await EgServiceChildJournal(client_factory).reserve(_record(), caller)
