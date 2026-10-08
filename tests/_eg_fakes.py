"""Shared fakes for tests that drive EG through generated senders.

The fake engine answers at ``client._send``, so every generated sender, model
and decoder runs for real. A subclass answers one method or ConnectorPack op
with an ``_on_<name>`` handler.
"""

from __future__ import annotations

from typing import Any

from agent_utilities.knowledge_graph.core.session import GraphSession
from agent_utilities.orchestration.action_policy import (
    ActionDecision,
    ActionRequest,
    PolicyDisposition,
    PolicyReceipt,
)
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext


class FakeEgEngine:
    """One fake EG transport shared by every graph view."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.graph_compute = self

    def for_graph(self, _graph: str) -> Any:
        return type("View", (), {"async_client": self})()

    async def _send(
        self, method: str, params: Any, graph: Any, *, idempotency_key: Any = None
    ) -> Any:
        op = (params or {}).get("op") if method == "ConnectorPack" else None
        name = op["op"] if isinstance(op, dict) else method
        self.calls.append((name, graph, params))
        handler = getattr(self, f"_on_{name}".replace("-", "_"), None)
        if handler is None:
            raise AssertionError(f"unexpected EG call {name}")
        return handler(params, idempotency_key)


class AllowPolicy:
    """An action policy that approves every request."""

    def decide(self, request: ActionRequest) -> ActionDecision:
        return ActionDecision(
            decision="allow",
            tier="auto_notify",
            request=request,
            receipt=PolicyReceipt(
                receipt_id="action_decision:test",
                request_digest=request.digest(),
                disposition=PolicyDisposition.APPROVE,
                policy_origin="test",
            ),
        )


def service_session(tenant: str, graph: str) -> GraphSession:
    """GraphOS's verified process session with the pack-import scopes."""

    return GraphSession(
        actor=ActorContext(
            actor_id="service:graph-os",
            actor_type=ActorType.AUTOMATED_SERVICE,
            tenant_id=tenant,
            authenticated=True,
        ),
        tenant=tenant,
        scopes=frozenset({"agent:pack-control", "connector:catalog-attest"}),
        graph=graph,
        audience="graph-os",
        policy_version="policy-a",
    )
