"""Durable, owner-stamped service child execution contract.

The existing shared multiplexer runtime is intentionally not adapted here: its
transient-death retry cannot prove whether a service child performed an effect.
A serving root must bind a durable journal and a typed one-shot transport.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class ServiceChildOutcomeUnknown(RuntimeError):
    """The child may have performed the effect; recovery must inspect its record."""


@dataclass(frozen=True, slots=True)
class ServiceChildRecord:
    record_id: str
    owner_ref: str
    principal: str
    tenant: str
    server: str
    tool: str
    subject_id: str
    argument_digest: str
    request_id: str

    @property
    def target(self) -> str:
        return f"fleet:{self.server}/{self.tool}"


@dataclass(frozen=True, slots=True)
class DurableReservation:
    record_id: str
    owner_ref: str
    target: str
    subject_id: str
    audit_ref: str
    recovery_ref: str
    durable: bool
    created: bool


class ServiceChildJournal(Protocol):
    """EG-backed store must persist before dispatch and retain unknown outcomes."""

    async def reserve(self, record: ServiceChildRecord) -> DurableReservation: ...

    async def succeeded(self, record_id: str, result_digest: str) -> bool: ...

    async def outcome_unknown(self, record_id: str, reason: str) -> bool: ...


class ServiceChildTransport(Protocol):
    """One child attempt; never calls ChildRuntime.call_tool's retry loop."""

    async def call_service_child_once(
        self, record: ServiceChildRecord, arguments: Mapping[str, Any]
    ) -> Any: ...


ToolFor = Callable[[Any, str, str], Awaitable[Any]]


def _digest(value: Any) -> str:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json", by_alias=True)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("service child payload is not JSON") from exc
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("service child payload exceeds boundary")
    return hashlib.sha256(encoded).hexdigest()


def _owner_ref(principal: str) -> str:
    return "principal:sha256:" + hashlib.sha256(principal.encode()).hexdigest()


class ServiceChildAdapter:
    """Record a verified owner and target before exactly one child attempt."""

    def __init__(
        self,
        *,
        journal: ServiceChildJournal,
        transport: ServiceChildTransport,
        admitted_tool: ToolFor,
    ) -> None:
        self._journal = journal
        self._transport = transport
        self._admitted_tool = admitted_tool

    async def __call__(
        self,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        caller: Any,
        owner_ref: str,
    ) -> Any:
        self._verify_caller(caller, owner_ref)
        item = await self._admitted_tool(caller, server, tool)
        if (
            item.credential_mode != "service"
            or not item.subject_id
            or not item.executor_scopes
            or not item.required_scopes.issubset(caller.effective_scopes)
        ):
            raise PermissionError("service child authority is incomplete")
        argument_digest = _digest(arguments)
        record_id = _digest(
            {
                "tenant": caller.tenant,
                "owner_ref": owner_ref,
                "request_id": caller.request_id,
                "server": server,
                "tool": tool,
                "arguments": argument_digest,
            }
        )
        record = ServiceChildRecord(
            record_id=record_id,
            owner_ref=owner_ref,
            principal=caller.principal,
            tenant=caller.tenant,
            server=server,
            tool=tool,
            subject_id=item.subject_id,
            argument_digest=argument_digest,
            request_id=caller.request_id,
        )
        reservation = await self._journal.reserve(record)
        if not self._reserved(record, reservation):
            raise RuntimeError("service child durable reservation is invalid")
        if not reservation.created:
            raise ServiceChildOutcomeUnknown("service child request already reserved")
        try:
            result = await self._transport.call_service_child_once(record, arguments)
        except BaseException as exc:
            if not await self._journal.outcome_unknown(record_id, type(exc).__name__):
                raise RuntimeError(
                    "service child recovery record is unavailable"
                ) from exc
            raise ServiceChildOutcomeUnknown(
                "service child outcome requires recovery"
            ) from exc
        child_error = (
            result.get("isError", result.get("is_error", False))
            if isinstance(result, Mapping)
            else getattr(result, "is_error", getattr(result, "isError", False))
        )
        if child_error:
            await self._journal.outcome_unknown(record_id, "child_error")
            raise ServiceChildOutcomeUnknown(
                "service child reported an uncertain effect"
            )
        try:
            stored = await self._journal.succeeded(record_id, _digest(result))
        except Exception as exc:
            await self._journal.outcome_unknown(record_id, "result_persist_failed")
            raise ServiceChildOutcomeUnknown(
                "service child result was not persisted"
            ) from exc
        if not stored:
            await self._journal.outcome_unknown(record_id, "result_persist_failed")
            raise ServiceChildOutcomeUnknown("service child result was not persisted")
        return result

    @staticmethod
    def _verify_caller(caller: Any, owner_ref: str) -> None:
        session = caller.session
        if not caller.authenticated or session is None:
            raise PermissionError("verified caller session is required")
        session.ensure_authority_current()
        actor = session.actor
        actor.ensure_credential_current()
        if (
            actor.authenticated is not True
            or str(actor.actor_id) != caller.principal
            or str(actor.tenant_id) != caller.tenant
            or str(session.tenant) != caller.tenant
            or not isinstance(caller.request_id, str)
            or not caller.request_id
            or owner_ref != _owner_ref(caller.principal)
        ):
            raise PermissionError("service child owner is not verified")

    @staticmethod
    def _reserved(record: ServiceChildRecord, receipt: DurableReservation) -> bool:
        return (
            isinstance(receipt, DurableReservation)
            and receipt.durable is True
            and receipt.record_id == record.record_id
            and receipt.owner_ref == record.owner_ref
            and receipt.target == record.target
            and receipt.subject_id == record.subject_id
            and bool(receipt.audit_ref)
            and bool(receipt.recovery_ref)
        )


def service_child_call_for_mux(
    *, mux: Any, journal: ServiceChildJournal, admitted_tool: ToolFor
) -> ServiceChildAdapter:
    """Refuse the old retrying multiplexer until a one-shot service port exists."""
    if not callable(getattr(mux, "call_service_child_once", None)):
        raise RuntimeError("multiplexer lacks one-shot service child transport")
    return ServiceChildAdapter(
        journal=journal, transport=mux, admitted_tool=admitted_tool
    )
