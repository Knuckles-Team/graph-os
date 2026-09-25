"""EG mutation contexts graph-os mints for its Decide consumers.

AU never mints an ``AgentLibraryMutationContext`` (the decide-consumers
contract): graph-os, the process that owns the policy gate, does — exactly
like AU's pack-import authority. Each mutation (a ``DecisionCommit``, an
assembled agent's ``AgentLibrary.publish``, a routed ``AgentGraph.publish``)
first needs an effect-authorizing AU ``ActionPolicy`` receipt bound to that
exact target by the verified process session; only then is EG's generated
context built. A denied or absent receipt raises
:class:`DecideCommitRefused`, and call sites fall back rather than act on an
unrecorded or unpublished decision.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

__all__ = [
    "DecideCommitRefused",
    "MutationAuthority",
    "decision_commit_context",
]


class DecideCommitRefused(RuntimeError):
    """No verified, policy-authorized context exists for this mutation."""


def _opaque_principal(actor_id: str) -> str:
    normalized = actor_id.strip()
    if normalized.startswith("principal:sha256:"):
        return normalized
    return "principal:sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MutationAuthority:
    """Policy receipt -> ``AgentLibraryMutationContext`` for one session."""

    def __init__(self, session: Any, policy: Any) -> None:
        self._session = session
        self._policy = policy

    @property
    def principal(self) -> str:
        """The session's opaque principal: EG's owner digest for its leases."""
        return _opaque_principal(str(self._session.actor.actor_id))

    def _receipt(self, kind: str, target: str) -> Any:
        from agent_utilities.orchestration.action_policy import ActionRequest

        request = ActionRequest(
            kind=kind,
            target=target,
            params={"tenant_id": str(self._session.tenant)},
            source="graph-os",
            reason=f"{kind} for a Decide consumer",
            actor_id=str(self._session.actor.actor_id),
        )
        decision = self._policy.decide(request)
        receipt = decision.receipt
        if (
            not decision.allowed
            or receipt is None
            or not receipt.authorizes_effect
            or receipt.request_digest != request.digest()
        ):
            raise DecideCommitRefused(f"{kind} is not authorized by policy")
        return receipt

    def context(
        self, *, kind: str, target: str, purpose_id: str, idempotency_key: str
    ) -> Any:
        """EG's generated mutation context, bound to a fresh policy receipt."""
        from epistemic_graph.generated.connector_pack import (
            AgentLibraryMutationContext,
        )

        receipt = self._receipt(kind, target)
        principal = _opaque_principal(str(self._session.actor.actor_id))
        return AgentLibraryMutationContext(
            request_id=secrets.randbits(63),
            principal=principal,
            caller_principal=principal,
            attempt_nonce=secrets.token_hex(32),
            tenant_id=str(self._session.tenant),
            actor_scope=principal,
            purpose_id=purpose_id,
            policy_revision=str(self._session.policy_version),
            policy_digest=f"sha256:{receipt.request_digest}",
            policy_decision_id=str(receipt.receipt_id),
            idempotency_key=idempotency_key,
            expected_revision=None,
            trace_id=self._session.trace_context,
            created_at_ms=int(time.time() * 1000),
        )


def decision_commit_context(
    session: Any, policy: Any
) -> Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]:
    """The ``commit_context`` provider AU's Assembler commits through."""
    authority = MutationAuthority(session, policy)

    async def provide(record: Mapping[str, Any]) -> Mapping[str, Any]:
        context = authority.context(
            kind="decision_commit",
            target=str(record.get("record_id") or ""),
            purpose_id="decision:commit",
            idempotency_key=f"decision:{record.get('record_digest', '')}",
        )
        return {
            "context": context.model_dump(mode="json"),
            "record": dict(record),
            "expected_catalog_digest": str(record["inputs"]["catalog_digest"]),
        }

    return provide
