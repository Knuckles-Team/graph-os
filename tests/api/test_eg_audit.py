"""Caller-bound EG audit reservation and outcome contracts."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from graph_os.api.invoke.eg_audit import EgAuditAdapter
from graph_os.api.invoke.steps import VerifiedCaller
from graph_os.api.registry import AuditClass


def actor(*, scopes: frozenset[str] = frozenset({"security:audit-write"})) -> VerifiedCaller:
    return VerifiedCaller(
        principal="user:1",
        tenant="tenant-a",
        effective_scopes=scopes,
        engine_claims={"principal": "user:1", "tenant": "tenant-a", "scopes": list(scopes)},
        principal_kind="human",
        request_id="request:1",
    )


def event() -> dict[str, str]:
    return {
        "op": "items.change",
        "surface": "mcp",
        "tenant": "tenant-a",
        "principal": "user:1",
        "plan_digest": "a" * 64,
        "result_status": "PENDING",
        "request_id": "request:1",
    }


@pytest.mark.asyncio
async def test_reservation_and_outcome_use_verified_actor_and_durable_receipts() -> None:
    seen: list[tuple[str, Any]] = []

    class Graph:
        async def audit_append(self, **kwargs: Any) -> dict[str, Any]:
            seen.append(("append", kwargs))
            return {"graph": "tenant-a", "seq": len(seen), "entry_sha256": "b" * 64}

    class Client:
        graph = Graph()

        @contextmanager
        def use_verified_context(self, claims: Any):
            seen.append(("claims", claims))
            yield self

    async def get_client(tenant: str) -> Client:
        assert tenant == "tenant-a"
        return Client()

    adapter = EgAuditAdapter(get_client)
    caller = actor()
    ref = await adapter.preflight(event(), AuditClass.EVENT, caller)
    assert ref == "request:1"
    outcome = {**event(), "result_status": "OK", "audit_ref": ref}
    await adapter.write(outcome, AuditClass.EVENT, caller)
    assert [entry[1]["status"] for entry in seen if entry[0] == "append"] == [
        "reserved", "ok"
    ]
    assert all(entry[1] == caller.engine_claims for entry in seen if entry[0] == "claims")


@pytest.mark.asyncio
async def test_missing_grant_or_wrong_graph_blocks_dispatch_reservation() -> None:
    async def never(_: str) -> Any:
        raise AssertionError("ungranted audit must not acquire a client")

    with pytest.raises(PermissionError, match="grant"):
        await EgAuditAdapter(never).preflight(event(), AuditClass.EVENT, actor(scopes=frozenset()))

    class Client:
        @contextmanager
        def use_verified_context(self, claims: Any):
            yield self

        class graph:
            @staticmethod
            async def audit_append(**kwargs: Any) -> dict[str, Any]:
                return {"graph": "other-tenant", "seq": 1, "entry_sha256": "b" * 64}

    async def wrong(_: str) -> Client:
        return Client()

    with pytest.raises(RuntimeError, match="receipt"):
        await EgAuditAdapter(wrong).preflight(event(), AuditClass.EVENT, actor())


@pytest.mark.asyncio
async def test_audit_event_cannot_claim_another_actor() -> None:
    async def never(_: str) -> Any:
        raise AssertionError("forged event must fail before client acquisition")

    forged = {**event(), "principal": "user:2"}
    with pytest.raises(ValueError, match="identity"):
        await EgAuditAdapter(never).preflight(forged, AuditClass.EVENT, actor())
