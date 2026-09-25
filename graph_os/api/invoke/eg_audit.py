"""Bind the operation audit boundary to EG's durable, caller-bound audit chain."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.invoke.executor import ClientFactory
from graph_os.api.invoke.steps import VerifiedCaller
from graph_os.api.registry import AuditClass

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
_DIGEST = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True, slots=True)
class EgAuditAdapter:
    """Use the same verified EG carrier for reservation and outcome.

    ``client_factory`` must return a client pinned to the caller's tenant graph.
    The EG receipt is checked against that graph before the operation proceeds.
    A missing caller audit grant fails before mutation dispatch; no service
    identity is substituted for the actor whose operation is recorded.
    """

    client_factory: ClientFactory

    @staticmethod
    def require_contract() -> None:
        """Fail startup if the installed EG wheel predates AuditAppend."""

        try:
            from epistemic_graph.client import GraphOperationsClient
        except ImportError as exc:
            raise ValueError("EG audit contract is unavailable") from exc
        if not callable(getattr(GraphOperationsClient, "audit_append", None)):
            raise ValueError("EG audit contract is unavailable")

    @staticmethod
    def _request_id(value: str) -> str:
        if value:
            if _REQUEST_ID.fullmatch(value) is None:
                raise ValueError("invalid audit request identity")
            return value
        return str(uuid.uuid4())

    @staticmethod
    def _fields(event: Mapping[str, str], caller: VerifiedCaller) -> dict[str, str]:
        if event.get("tenant") != caller.tenant or event.get("principal") != caller.principal:
            raise ValueError("audit identity mismatch")
        digest = event.get("plan_digest", "")
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("invalid audit params digest")
        op = event.get("op", "")
        surface = event.get("surface", "")
        if not op or len(op) > 128 or not surface or len(surface) > 32:
            raise ValueError("invalid audit operation")
        if event.get("request_id") != caller.request_id:
            raise ValueError("audit request identity mismatch")
        return {"op": op, "surface": surface, "params_sha256": digest}

    async def _append(
        self,
        event: Mapping[str, str],
        audit_class: AuditClass,
        caller: VerifiedCaller,
        *,
        status: str,
        request_id: str,
    ) -> Mapping[str, Any]:
        fields = self._fields(event, caller)
        if audit_class not in (AuditClass.EVENT, AuditClass.IDENTITY_CHAIN):
            raise ValueError("mutation audit class absent")
        if "security:audit-write" not in caller.effective_scopes:
            raise PermissionError("caller audit grant unavailable")
        client = await self.client_factory(caller.tenant)
        graph = getattr(client, "graph", None)
        append = getattr(graph, "audit_append", None)
        context = getattr(client, "use_verified_context", None)
        if not callable(append) or not callable(context):
            raise RuntimeError("EG audit client is unavailable")
        with context(caller.engine_claims):
            receipt = await append(
                **fields,
                status=status,
                request_id=request_id,
                identity_chain=audit_class == AuditClass.IDENTITY_CHAIN,
            )
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("graph") != caller.tenant
            or not isinstance(receipt.get("seq"), int)
            or receipt["seq"] < 0
            or _DIGEST.fullmatch(str(receipt.get("entry_sha256", ""))) is None
        ):
            raise RuntimeError("EG audit receipt is invalid")
        return receipt

    async def preflight(
        self, event: Mapping[str, str], audit_class: AuditClass, caller: VerifiedCaller
    ) -> str:
        request_id = self._request_id(caller.request_id)
        await self._append(
            event, audit_class, caller, status="reserved", request_id=request_id
        )
        return request_id

    async def write(
        self, event: Mapping[str, str], audit_class: AuditClass, caller: VerifiedCaller
    ) -> None:
        request_id = event.get("audit_ref", "")
        if _REQUEST_ID.fullmatch(request_id) is None:
            raise ValueError("audit reservation reference is invalid")
        result_status = event.get("result_status", "")
        status = "ok" if result_status == "OK" else "denied" if result_status in {
            "POLICY_DENIED", "SUBJECT_ACCESS_DENIED", "PRINCIPAL_NOT_ALLOWED"
        } else "error"
        await self._append(
            event, audit_class, caller, status=status, request_id=request_id
        )
