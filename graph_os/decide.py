"""graph-os boot composition of AU's Decide consumers (lane decide-consumers contract).

At startup, once the engine and the process's verified session exist,
graph-os installs:

* AU's :class:`~agent_utilities.decide.DecisionRunner` for the process tenant,
  over EG's generated ``Decide``/``DecisionLog`` senders
  (:class:`~agent_utilities.decide.transport.GeneratedTransport`). Sync call
  sites drive it on a dedicated decision loop that graph-os owns.
* AU's :class:`~agent_utilities.decide.consumers.assembly.Assembler` (EG
  ``AgentAssemble``) with the ``DecisionCommit`` mutation-context provider
  that AU deliberately never mints itself.

Point bindings are resolved from the Agent Library: each point's
``decide.schema.<question_id>`` FeatureSchema component (and an optional
promoted ``decide.head.<question_id>`` DecisionHead) at its current
revision. An unpublished point stays unbound and runs its deterministic
fallback, so installing the runner changes no behaviour until an operator
publishes a schema.

The commit context is minted exactly like AU's pack-import authority: the
verified process session and an effect-authorizing AU ``ActionPolicy``
receipt bound to the exact record, then EG's generated
``AgentLibraryMutationContext``. A denied or unavailable receipt raises
:class:`DecideCommitRefused`; call sites then use their fallback rather than
act on an unrecorded decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DecideCommitRefused",
    "DecideComposition",
    "DecideLoop",
    "current_decide",
    "decision_commit_context",
    "install_decide",
    "install_decide_at_boot",
    "resolve_bindings",
]

_ACTION_KIND = "decision_commit"
_SYNC_TIMEOUT_S = 10.0


class DecideCommitRefused(RuntimeError):
    """No verified, policy-authorized context exists for this DecisionCommit."""


class DecideLoop:
    """A daemon event loop graph-os owns for sync decision call sites."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self.loop.run_forever, name="graph-os-decide", daemon=True
        )
        self._thread.start()

    def run(self, call: Awaitable[Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(_awaited(call), self.loop)
        return future.result(timeout=_SYNC_TIMEOUT_S)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=_SYNC_TIMEOUT_S)


async def _awaited(call: Awaitable[Any]) -> Any:
    return await call


def _dependency(entry: Any) -> dict[str, Any]:
    kind = getattr(entry.kind, "value", entry.kind)
    return {
        "component_id": str(entry.component_id),
        "kind": str(kind),
        "definition_digest": str(entry.definition_digest),
    }


async def _current(components: Any, tenant: str, component_id: str) -> Any:
    from epistemic_graph.generated.agent_component import AgentComponentOpCurrent

    return await components.current(
        AgentComponentOpCurrent(
            op="current", component_id=component_id, tenant_id=tenant
        )
    )


async def resolve_bindings(components: Any, tenant: str) -> Any:
    """``StaticBindings`` for every point whose FeatureSchema is published."""
    from agent_utilities.decide import POINTS, StaticBindings
    from agent_utilities.decide.points import Binding

    bound: dict[str, Any] = {}
    for question_id, point in POINTS.items():
        schema = await _current(components, tenant, point.schema_component_id)
        if schema is None:
            continue
        head = await _current(components, tenant, f"decide.head.{question_id}")
        bound[question_id] = Binding(
            feature_schema=_dependency(schema),
            policy={"policy": "default"},
            head=None if head is None else _dependency(head),
        )
    return StaticBindings(bound)


def _opaque_principal(actor_id: str) -> str:
    normalized = actor_id.strip()
    if normalized.startswith("principal:sha256:"):
        return normalized
    return "principal:sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _receipt(policy: Any, session: Any, record: Mapping[str, Any]) -> Any:
    from agent_utilities.orchestration.action_policy import ActionRequest

    request = ActionRequest(
        kind=_ACTION_KIND,
        target=str(record.get("record_id") or ""),
        params={"tenant_id": str(session.tenant)},
        source="graph-os",
        reason="commit an assembly DecisionRecord",
        actor_id=str(session.actor.actor_id),
    )
    decision = policy.decide(request)
    receipt = decision.receipt
    if (
        not decision.allowed
        or receipt is None
        or not receipt.authorizes_effect
        or receipt.request_digest != request.digest()
    ):
        raise DecideCommitRefused("DecisionCommit is not authorized by policy")
    return receipt


def decision_commit_context(
    session: Any, policy: Any
) -> Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]:
    """The ``commit_context`` provider AU's Assembler commits through."""

    async def provide(record: Mapping[str, Any]) -> Mapping[str, Any]:
        from epistemic_graph.generated.connector_pack import (
            AgentLibraryMutationContext,
        )

        receipt = _receipt(policy, session, record)
        principal = _opaque_principal(str(session.actor.actor_id))
        context = AgentLibraryMutationContext(
            request_id=secrets.randbits(63),
            principal=principal,
            caller_principal=principal,
            attempt_nonce=secrets.token_hex(32),
            tenant_id=str(session.tenant),
            actor_scope=principal,
            purpose_id="decision:commit",
            policy_revision=str(session.policy_version),
            policy_digest=f"sha256:{receipt.request_digest}",
            policy_decision_id=str(receipt.receipt_id),
            idempotency_key=f"decision:{record.get('record_digest', '')}",
            expected_revision=None,
            trace_id=session.trace_context,
            created_at_ms=int(time.time() * 1000),
        )
        return {
            "context": context.model_dump(mode="json"),
            "record": dict(record),
            "expected_catalog_digest": str(record["inputs"]["catalog_digest"]),
        }

    return provide


@dataclass(frozen=True)
class DecideComposition:
    """What boot installed: the runner, the assembler and their loop."""

    runner: Any
    assembler: Any
    loop: DecideLoop

    def uninstall(self) -> None:
        from agent_utilities import decide
        from agent_utilities.decide.consumers.assembly import install_assembler

        decide.install_runner(None)
        install_assembler(None)
        _INSTALLED[0] = None
        self.loop.stop()


_INSTALLED: list[DecideComposition | None] = [None]


def current_decide() -> DecideComposition | None:
    """The installed composition, or ``None`` (every call site falls back)."""
    return _INSTALLED[0]


def install_decide(client: Any, session: Any, policy: Any) -> DecideComposition:
    """Install the process runner and assembler for ``session``'s tenant."""
    from agent_utilities import decide
    from agent_utilities.decide.consumers.assembly import Assembler, install_assembler
    from agent_utilities.decide.transport import GeneratedTransport
    from agent_utilities.layers.clients import LayerClients

    tenant = str(session.tenant)
    loop = DecideLoop()
    layers = LayerClients.for_session(client, session)
    bindings = loop.run(resolve_bindings(layers.components, tenant))
    runner = decide.DecisionRunner(
        transport=GeneratedTransport(client=client, graph=tenant, loop=loop.loop),
        bindings=bindings,
        tenant=tenant,
    )
    assembler = Assembler(
        layers.graphs, tenant, commit_context=decision_commit_context(session, policy)
    )
    decide.install_runner(runner)
    install_assembler(assembler, run=loop.run)
    composition = DecideComposition(runner, assembler, loop)
    _INSTALLED[0] = composition
    logger.info(
        "Decide installed for tenant: %d of %d points bound",
        len(bindings.by_question),
        len(decide.POINTS),
    )
    return composition


def install_decide_at_boot(
    graph_client_for: Callable[[str], Any], session: Any, engine: Any
) -> DecideComposition | None:
    """Boot hook: install Decide, or keep every point on its fallback (logged)."""
    try:
        from agent_utilities.orchestration.action_policy import get_action_policy

        return install_decide(
            graph_client_for(str(session.tenant)), session, get_action_policy(engine)
        )
    except (ImportError, RuntimeError, TimeoutError) as exc:
        logger.warning(
            "Decide not installed (%s: %s); every decision point runs its "
            "deterministic fallback",
            type(exc).__name__,
            exc,
        )
        return None
