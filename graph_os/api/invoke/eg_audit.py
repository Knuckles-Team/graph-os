"""Adapter for the public EG AuditAppend contract; no effect journal substitute."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.invoke.executor import ClientFactory
from graph_os.api.invoke.steps import VerifiedCaller, claim_values
from graph_os.api.registry import AuditClass

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
_DIGEST = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True, slots=True)
class EgAuditAdapter:
    """Use caller authority and validate the durable reservation/outcome link.

    The provider owns audit scope checks. Startup admission must qualify the
    installed provider; method presence alone is not durable-authority evidence.
    """

    client_factory: ClientFactory

    @staticmethod
    def require_contract() -> None:
        try:
            from epistemic_graph.client import AdminClient
        except ImportError as exc:
            raise ValueError("EG audit contract is unavailable") from exc
        method = getattr(AdminClient, "audit_append", None)
        required = {
            "op",
            "surface",
            "params_sha256",
            "status",
            "request_id",
            "audit_class",
        }
        if (
            not callable(method)
            or not required <= inspect.signature(method).parameters.keys()
        ):
            raise ValueError("EG audit contract is unavailable")

    @staticmethod
    def _request_id(event: Mapping[str, str], caller: VerifiedCaller) -> str:
        # AuditAppend binds surface into its fingerprint. Keep its reservation
        # distinct per surface while the effect owner's key remains shared.
        return hashlib.sha256(
            (caller.request_id + "\0" + event.get("surface", "")).encode()
        ).hexdigest()

    @staticmethod
    def _fields(event: Mapping[str, str], caller: VerifiedCaller) -> dict[str, str]:
        if (
            event.get("tenant") != caller.tenant
            or event.get("principal") != caller.principal
        ):
            raise ValueError("audit identity mismatch")
        digest = event.get("plan_digest", "")
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("invalid audit params digest")
        op, surface = event.get("op", ""), event.get("surface", "")
        if not op or len(op) > 128 or surface not in {"mcp", "http", "a2a", "console"}:
            raise ValueError("invalid audit operation")
        if (
            event.get("request_id") != caller.request_id
            or _REQUEST_ID.fullmatch(caller.request_id) is None
        ):
            raise ValueError("audit request identity mismatch")
        return {"op": op, "surface": surface, "params_sha256": digest}

    async def _append(
        self,
        event: Mapping[str, str],
        audit_class: AuditClass,
        caller: VerifiedCaller,
        *,
        status: str,
    ) -> Mapping[str, Any]:
        fields = self._fields(event, caller)
        if audit_class not in (AuditClass.EVENT, AuditClass.IDENTITY_CHAIN):
            raise ValueError("mutation audit class absent")
        client = await self.client_factory(caller.tenant)
        append = getattr(getattr(client, "admin", None), "audit_append", None)
        context = getattr(client, "use_verified_context", None)
        if not callable(append) or not callable(context):
            raise RuntimeError("EG audit client is unavailable")
        with context(claim_values(caller.engine_claims)):
            receipt = await append(
                **fields,
                status=status,
                request_id=self._request_id(event, caller),
                audit_class=audit_class.value,
            )
        _check_receipt(receipt, caller.tenant, status)
        return receipt

    async def preflight(
        self, event: Mapping[str, str], audit_class: AuditClass, caller: VerifiedCaller
    ) -> str:
        receipt = await self._append(event, audit_class, caller, status="reserved")
        return json.dumps(
            [self._request_id(event, caller), receipt["reservation_seq"]],
            separators=(",", ":"),
        )

    def _reservation_seq(self, event: Mapping[str, str], caller: VerifiedCaller) -> int:
        try:
            reference = json.loads(event.get("audit_ref", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid audit reservation reference") from exc
        if (
            not isinstance(reference, list)
            or len(reference) != 2
            or reference[0] != self._request_id(event, caller)
            or not _sequence(reference[1])
        ):
            raise ValueError("invalid audit reservation reference")
        return int(reference[1])

    async def write(
        self, event: Mapping[str, str], audit_class: AuditClass, caller: VerifiedCaller
    ) -> None:
        reservation_seq = self._reservation_seq(event, caller)
        code = event.get("result_status", "")
        # AuditAppend has no uncertain terminal phase. Keep its reservation
        # unresolved for reconciliation; do not manufacture a definite error.
        if code in {"INDETERMINATE", "TIMEOUT"}:
            return
        receipt = await self._append(
            event, audit_class, caller, status=_outcome_status(code)
        )
        if receipt["reservation_seq"] != reservation_seq:
            raise RuntimeError("EG audit outcome links a different reservation")


_DENIED_CODES = frozenset(
    {
        "POLICY_DENIED",
        "SUBJECT_ACCESS_DENIED",
        "PRINCIPAL_NOT_ALLOWED",
        "UNAUTHENTICATED",
        "SCOPE_REQUIRED",
    }
)


def _outcome_status(code: str) -> str:
    if code == "OK":
        return "ok"
    return "denied" if code in _DENIED_CODES else "error"


def _sequence(value: Any) -> bool:
    return type(value) is int and value >= 0


def _outcome_linked(receipt: Mapping[str, Any]) -> bool:
    if "outcome_seq" not in receipt:
        return False
    outcome = receipt["outcome_seq"]
    return outcome is None or (
        type(outcome) is int and outcome >= receipt["reservation_seq"]
    )


def _check_receipt(receipt: Any, tenant: str, status: str) -> None:
    """Validate the provider receipt and its reservation/outcome phase link."""
    if not isinstance(receipt, Mapping) or receipt.get("graph") != tenant:
        raise RuntimeError("EG audit receipt graph is invalid")
    if not all(_sequence(receipt.get(name)) for name in ("seq", "reservation_seq")):
        raise RuntimeError("EG audit receipt sequence is invalid")
    if not _outcome_linked(receipt):
        raise RuntimeError("EG audit outcome link is invalid")
    if (
        not isinstance(receipt.get("replayed"), bool)
        or _DIGEST.fullmatch(str(receipt.get("entry_sha256", ""))) is None
    ):
        raise RuntimeError("EG audit receipt is invalid")
    expected = (
        receipt["reservation_seq"] if status == "reserved" else receipt["outcome_seq"]
    )
    if receipt["seq"] != expected:
        raise RuntimeError("EG audit receipt phase mismatch")
