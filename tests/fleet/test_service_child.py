"""Owner-stamped service child calls fail closed without durable evidence."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from graph_os.fleet.catalog_items import CatalogItem
from graph_os.fleet.service_child import (
    DurableReservation,
    ServiceChildAdapter,
    ServiceChildOutcomeUnknown,
    service_child_call_for_mux,
)


class Journal:
    def __init__(self, *, created: bool = True, mismatched: bool = False):
        self.created = created
        self.mismatched = mismatched
        self.records = []
        self.succeeded_ids = []
        self.unknown = []

    async def reserve(self, record):
        self.records.append(record)
        return DurableReservation(
            record_id=record.record_id,
            owner_ref=record.owner_ref,
            target="fleet:wrong/tool" if self.mismatched else record.target,
            subject_id=record.subject_id,
            audit_ref="audit:42",
            recovery_ref="recovery:42",
            durable=True,
            created=self.created,
        )

    async def succeeded(self, record_id, result_digest):
        self.succeeded_ids.append((record_id, result_digest))
        return True

    async def outcome_unknown(self, record_id, reason):
        self.unknown.append((record_id, reason))
        return True


class Transport:
    def __init__(self, *, fail: bool = False, result=None):
        self.fail = fail
        self.result = {"ok": True} if result is None else result
        self.calls = []

    async def call_service_child_once(self, record, arguments):
        self.calls.append((record, arguments))
        if self.fail:
            raise ConnectionError("child may have executed")
        return self.result


def _caller():
    actor = SimpleNamespace(
        authenticated=True,
        actor_id="alice",
        tenant_id="tenant-a",
        ensure_credential_current=lambda: None,
    )
    session = SimpleNamespace(
        actor=actor,
        tenant="tenant-a",
        ensure_authority_current=lambda: None,
    )
    return SimpleNamespace(
        authenticated=True,
        principal="alice",
        tenant="tenant-a",
        effective_scopes=frozenset({"mcp:delegate", "domain:read"}),
        request_id="req-1",
        session=session,
    )


def _owner_ref():
    return "principal:sha256:" + hashlib.sha256(b"alice").hexdigest()


async def _admitted(_caller, _server, _tool):
    return CatalogItem(
        "fleet:tool:s/run",
        "tool",
        "run",
        server="s",
        credential_mode="service",
        required_scopes=frozenset({"domain:read"}),
        executor_scopes=frozenset({"fleet:execute"}),
        subject_id="eg-server-42",
    )


@pytest.mark.asyncio
async def test_owner_and_target_persist_before_one_service_attempt() -> None:
    journal, transport = Journal(), Transport()
    adapter = ServiceChildAdapter(
        journal=journal, transport=transport, admitted_tool=_admitted
    )
    result = await adapter("s", "run", {"secret": "value"}, _caller(), _owner_ref())
    assert result == {"ok": True}
    assert len(transport.calls) == 1
    record = journal.records[0]
    assert record.owner_ref == _owner_ref()
    assert record.target == "fleet:s/run"
    assert record.subject_id == "eg-server-42"
    assert record.argument_digest == hashlib.sha256(b'{"secret":"value"}').hexdigest()
    assert "secret" not in repr(record)
    assert journal.succeeded_ids[0][0] == record.record_id


@pytest.mark.asyncio
async def test_invalid_or_duplicate_reservation_never_dispatches() -> None:
    for journal in (Journal(mismatched=True), Journal(created=False)):
        transport = Transport()
        adapter = ServiceChildAdapter(
            journal=journal, transport=transport, admitted_tool=_admitted
        )
        with pytest.raises((RuntimeError, ServiceChildOutcomeUnknown)):
            await adapter("s", "run", {}, _caller(), _owner_ref())
        assert transport.calls == []


@pytest.mark.asyncio
async def test_uncertain_child_result_records_recovery_target() -> None:
    journal, transport = Journal(), Transport(fail=True)
    adapter = ServiceChildAdapter(
        journal=journal, transport=transport, admitted_tool=_admitted
    )
    with pytest.raises(ServiceChildOutcomeUnknown):
        await adapter("s", "run", {}, _caller(), _owner_ref())
    assert len(transport.calls) == 1
    assert journal.unknown == [(journal.records[0].record_id, "ConnectionError")]


@pytest.mark.asyncio
async def test_child_error_does_not_claim_success() -> None:
    journal, transport = Journal(), Transport(result={"isError": True})
    adapter = ServiceChildAdapter(
        journal=journal, transport=transport, admitted_tool=_admitted
    )
    with pytest.raises(ServiceChildOutcomeUnknown):
        await adapter("s", "run", {}, _caller(), _owner_ref())
    assert journal.succeeded_ids == []
    assert journal.unknown == [(journal.records[0].record_id, "child_error")]


@pytest.mark.asyncio
async def test_unverified_owner_and_old_multiplexer_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="one-shot"):
        service_child_call_for_mux(
            mux=SimpleNamespace(call_proxied_tool=lambda *_: None),
            journal=Journal(),
            admitted_tool=_admitted,
        )
    transport = Transport()
    adapter = ServiceChildAdapter(
        journal=Journal(), transport=transport, admitted_tool=_admitted
    )
    with pytest.raises(PermissionError, match="owner"):
        await adapter("s", "run", {}, _caller(), "principal:sha256:forged")
    assert transport.calls == []
