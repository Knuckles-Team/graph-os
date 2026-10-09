"""Ten-minute, single-use confirmation leases held by epistemic-graph."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from graph_os.api.invoke.steps import OpError, VerifiedCaller, claim_values

PLAN_TTL_MS = 600_000
MAX_SEALED_PLAN_BYTES = 16_384


def params_digest(params: Mapping[str, Any]) -> str:
    raw = json.dumps(
        claim_values(params),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanBinding:
    op_id: str
    params_digest: str
    principal: str
    tenant: str
    policy_revision: str
    registry_digest: str
    effect: str
    confirm: str

    def as_grant(self) -> dict[str, str]:
        return asdict(self)


def bind_plan(
    op: Any, params: Mapping[str, Any], caller: VerifiedCaller, registry_digest: str
) -> PlanBinding:
    return PlanBinding(
        op.id,
        params_digest(params),
        caller.principal,
        caller.tenant,
        caller.policy_revision,
        registry_digest,
        op.effect.value,
        op.confirm.value,
    )


class EgPlanStore:
    """Use EG's allowlisted control leases; caller supplies a service-authorized client."""

    def __init__(
        self, client: Any, *, lease_kind: str, seal_key: bytes | None = None
    ) -> None:
        if not isinstance(lease_kind, str) or not lease_kind:
            raise ValueError("configured plan lease kind is required")
        self._lease_kind = lease_kind
        self._leases = client.control_leases
        if seal_key is not None and len(seal_key) != 32:
            raise ValueError("plan seal key must be 32 bytes")
        self._cipher = AESGCM(seal_key) if seal_key is not None else None

    def _seal(self, plan_ref: str, params: Mapping[str, Any]) -> str:
        if self._cipher is None:
            raise RuntimeError("console plan seal key is unavailable")
        raw = json.dumps(
            claim_values(params),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        if len(raw) > MAX_SEALED_PLAN_BYTES:
            raise ValueError("console plan arguments exceed the sealed limit")
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, raw, plan_ref.encode())
        return urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def _open(self, plan_ref: str, sealed: str) -> dict[str, Any]:
        if self._cipher is None:
            raise RuntimeError("console plan seal key is unavailable")
        raw = urlsafe_b64decode(sealed.encode("ascii"))
        if len(raw) < 29 or len(raw) > MAX_SEALED_PLAN_BYTES + 28:
            raise ValueError("invalid sealed plan size")
        decoded = self._cipher.decrypt(raw[:12], raw[12:], plan_ref.encode())
        params = json.loads(decoded)
        if not isinstance(params, dict):
            raise ValueError("sealed plan arguments are invalid")
        return params

    async def issue(
        self, binding: PlanBinding, params: Mapping[str, Any] | None = None
    ) -> str:
        plan_ref = f"graphos_plan:{secrets.token_hex(24)}"
        now_ms = int(time.time() * 1000)
        grant = binding.as_grant()
        if binding.confirm == "console":
            if params is None or params_digest(params) != binding.params_digest:
                raise ValueError("console plan arguments do not match binding")
            grant["sealed_params"] = self._seal(plan_ref, params)
        answer = await self._leases.issue(
            tenant=binding.tenant,
            lease_id=plan_ref,
            kind=self._lease_kind,
            grant=grant,
            issued_at_ms=now_ms,
            expires_at_ms=now_ms + PLAN_TTL_MS,
            hard_expires_at_ms=now_ms + PLAN_TTL_MS,
            idempotency_key=f"issue:{plan_ref}",
        )
        if answer.get("outcome") != "issued":
            raise RuntimeError("EG did not issue graphos.plan lease")
        return plan_ref

    async def get_console_plan(
        self, plan_ref: str, caller: VerifiedCaller, registry_digest: str
    ) -> tuple[dict[str, Any] | None, OpError | None]:
        """Read only this caller's live, sealed plan for attended review."""

        lease = await self._leases.get(tenant=caller.tenant, lease_id=plan_ref)
        refusal = _live_lease_refusal(lease, self._lease_kind)
        if refusal is not None:
            return None, refusal
        grant = lease.get("grant")
        refusal = _console_grant_refusal(grant, caller, registry_digest)
        if refusal is not None:
            return None, refusal
        return self._console_params(plan_ref, grant)

    def _console_params(
        self, plan_ref: str, grant: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, OpError | None]:
        sealed = grant.get("sealed_params")
        if not isinstance(sealed, str):
            return None, OpError("PLAN_MISMATCH")
        try:
            params = self._open(plan_ref, sealed)
        except (ValueError, TypeError, InvalidTag, Base64Error):
            return None, OpError("PLAN_MISMATCH")
        if params_digest(params) != grant.get("params_digest"):
            return None, OpError("PLAN_MISMATCH")
        return {"plan_ref": plan_ref, "op": grant.get("op_id"), "params": params}, None

    async def validate(
        self, plan_ref: str, binding: PlanBinding
    ) -> tuple[dict[str, Any] | None, OpError | None]:
        lease = await self._leases.get(tenant=binding.tenant, lease_id=plan_ref)
        refusal = _live_lease_refusal(lease, self._lease_kind)
        if refusal is not None:
            return None, refusal
        grant = lease.get("grant")
        if not isinstance(grant, Mapping):
            return None, OpError("PLAN_MISMATCH")
        if (
            grant.get("registry_digest") != binding.registry_digest
            or grant.get("policy_revision") != binding.policy_revision
        ):
            return None, OpError("PLAN_STALE")
        if any(grant.get(key) != value for key, value in binding.as_grant().items()):
            return None, OpError("PLAN_MISMATCH")
        return lease, None

    async def consume(self, plan_ref: str, binding: PlanBinding) -> OpError | None:
        lease, refusal = await self.validate(plan_ref, binding)
        if refusal is not None:
            return refusal
        assert lease is not None
        answer = await self._leases.transition(
            tenant=binding.tenant,
            lease_id=plan_ref,
            expected_revision=int(lease["revision"]),
            to="consumed",
            idempotency_key=f"consume:{plan_ref}",
        )
        return None if answer.get("outcome") == "applied" else OpError("PLAN_EXPIRED")


def _live_lease_refusal(lease: Any, lease_kind: str) -> OpError | None:
    """Refuse a lease that is absent, foreign, inactive or past its hard expiry."""
    if not lease or lease.get("kind") != lease_kind:
        return OpError("PLAN_MISMATCH")
    if lease.get("status") != "active" or int(
        lease.get("hard_expires_at_ms", 0)
    ) <= int(time.time() * 1000):
        return OpError("PLAN_EXPIRED")
    return None


def _console_grant_refusal(
    grant: Any, caller: VerifiedCaller, registry_digest: str
) -> OpError | None:
    if not isinstance(grant, Mapping) or grant.get("confirm") != "console":
        return OpError("PLAN_MISMATCH")
    if (
        grant.get("principal") != caller.principal
        or grant.get("tenant") != caller.tenant
    ):
        return OpError("PLAN_MISMATCH")
    if (
        grant.get("policy_revision") != caller.policy_revision
        or grant.get("registry_digest") != registry_digest
    ):
        return OpError("PLAN_STALE")
    return None


class PlanStore(Protocol):
    """Durable lease authority required by the composition root."""

    async def issue(self, binding: PlanBinding, params: Mapping[str, Any]) -> str: ...

    async def validate(
        self, plan_ref: str, binding: PlanBinding
    ) -> tuple[Mapping[str, Any] | None, OpError | None]: ...

    async def consume(self, plan_ref: str, binding: PlanBinding) -> OpError | None: ...
