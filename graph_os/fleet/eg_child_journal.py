"""Caller-bound EG journal for one-attempt service-credential child calls."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from graph_os.fleet.service_child import DurableReservation, ServiceChildRecord

CallerClient = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class EgServiceChildJournal:
    """Bind durable child records to the verified caller's EG carrier.

    Child execution uses GraphOS's service identity, while these journal calls
    use the original caller's verified EG context. EG checks the caller's
    existing AuditAppend reservation and derives owner and tenant there.
    """

    client_factory: CallerClient

    @staticmethod
    def require_contract() -> None:
        from epistemic_graph.client import GraphOperationsClient

        for name in (
            "service_child_reserve",
            "service_child_get",
            "service_child_finish",
        ):
            if not callable(getattr(GraphOperationsClient, name, None)):
                raise RuntimeError("EG service child journal contract is unavailable")

    @contextmanager
    def _bound_graph(self, client: Any, caller: Any) -> Iterator[Any]:
        context = getattr(client, "use_verified_context", None)
        graph = getattr(client, "graph", None)
        if not callable(context) or graph is None:
            raise RuntimeError("caller-bound EG journal client is unavailable")
        with context(caller.engine_claims):
            yield graph

    @staticmethod
    def _identity(response: Any, caller: Any) -> Mapping[str, Any]:
        if (
            not isinstance(response, Mapping)
            or response.get("tenant", response.get("graph")) != caller.tenant
            or response.get("owner_ref")
            != "principal:sha256:"
            + hashlib.sha256(caller.principal.encode()).hexdigest()
        ):
            raise RuntimeError("EG child journal receipt identity is invalid")
        return response

    async def reserve(
        self, record: ServiceChildRecord, caller: Any
    ) -> DurableReservation:
        if record.tenant != caller.tenant or record.principal != caller.principal:
            raise PermissionError("child journal owner does not match caller")
        if not {"mcp:delegate", "security:audit-write"}.issubset(
            caller.effective_scopes
        ):
            raise PermissionError("child journal caller scopes are incomplete")
        binding = {
            "tenant": record.tenant,
            "owner_principal": record.principal,
            "owner_ref": record.owner_ref,
            "server": record.server,
            "tool": record.tool,
            "subject_id": record.subject_id,
            "argument_sha256": record.argument_digest,
            "audit_params_sha256": record.audit_params_sha256,
            "request_id": record.request_id,
            "policy_revision": record.policy_revision,
            "registry_revision": record.registry_revision,
            "scopes_sha256": record.scopes_sha256,
        }
        client = await self.client_factory(caller.tenant)
        with self._bound_graph(client, caller) as graph:
            reserve = getattr(graph, "service_child_reserve", None)
            if not callable(reserve):
                raise RuntimeError("EG child journal reserve is unavailable")
            response = await reserve(binding, audit_ref=record.request_id)
        row = self._identity(response, caller)
        return DurableReservation(
            record_id=row["record_id"],
            owner_ref=row["owner_ref"],
            target=row["target"],
            subject_id=row["subject_id"],
            audit_ref=row["audit_ref"],
            recovery_ref=row["recovery_ref"],
            durable=row["durable"],
            created=row["created"],
        )

    async def get(self, record_id: str, caller: Any) -> Mapping[str, Any] | None:
        client = await self.client_factory(caller.tenant)
        with self._bound_graph(client, caller) as graph:
            read = getattr(graph, "service_child_get", None)
            if not callable(read):
                raise RuntimeError("EG child journal read is unavailable")
            response = await read(record_id)
        if response is None:
            return None
        row = self._identity(response, caller)
        if row.get("record_id") != record_id:
            raise RuntimeError("EG child journal record identity is invalid")
        return row

    async def _finish(
        self,
        record_id: str,
        caller: Any,
        *,
        outcome: str,
        result_sha256: str | None = None,
        reason_code: str | None = None,
    ) -> bool:
        client = await self.client_factory(caller.tenant)
        with self._bound_graph(client, caller) as graph:
            finish = getattr(graph, "service_child_finish", None)
            if not callable(finish):
                raise RuntimeError("EG child journal finish is unavailable")
            response = await finish(
                record_id,
                outcome=outcome,
                result_sha256=result_sha256,
                reason_code=reason_code,
            )
        row = self._identity(response, caller)
        return row.get("record_id") == record_id and row.get("state") == outcome

    async def succeeded(self, record_id: str, result_digest: str, caller: Any) -> bool:
        return await self._finish(
            record_id, caller, outcome="succeeded", result_sha256=result_digest
        )

    async def outcome_unknown(self, record_id: str, reason: str, caller: Any) -> bool:
        return await self._finish(
            record_id, caller, outcome="outcome_unknown", reason_code=reason
        )
