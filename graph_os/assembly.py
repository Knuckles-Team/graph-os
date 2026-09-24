"""Budgeted agent and tool-subset assembly through EG ``AgentAssemble``.

GraphOS never decides an agent or tool subset itself. It states typed
requirements -- native task or capability IRIs, a context budget and the
candidate kinds -- and EG's ``AgentAssemble`` (DECIDE-LAYER-DESIGN §4.2)
proves the smallest covering selection against one tenant-bound agent-library
snapshot, answering with a ``DecisionRecord``. The read commits nothing; tool
exposure is evaluate-only per request (§10).

Free text is never mapped to tasks here: without caller-supplied IRIs the text
is sent only as its digest in ``unmapped_task_digests``, which EG answers with
an explicit abstention rather than a guess.

The method fails closed with :class:`AssemblyUnavailable` until the connected
engine advertises ``AgentAssemble`` and the generated client ships its sender.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AgentAssembly",
    "AssemblyAbstained",
    "AssemblyOutcome",
    "AssemblyRequirements",
    "AssemblyUnavailable",
    "build_assembly_request",
]

METHOD = "AgentAssemble"
_MAX_IRIS = 32


class AssemblyUnavailable(RuntimeError):
    """The connected engine does not serve ``AgentAssemble``."""


class AssemblyAbstained(RuntimeError):
    """EG proved no covering assembly; ``reasons`` says exactly why."""

    def __init__(self, record_id: str, reasons: Sequence[str]) -> None:
        super().__init__(f"assembly abstained ({', '.join(reasons) or 'no reason'})")
        self.record_id = record_id
        self.reasons = tuple(reasons)


@dataclass(frozen=True, slots=True)
class AssemblyRequirements:
    """What the assembled selection must cover, and within what budget."""

    text: str
    context_budget_tokens: int
    kinds: tuple[str, ...]
    task_iris: tuple[str, ...] = ()
    capability_iris: tuple[str, ...] = ()
    require_tools: bool = False

    def __post_init__(self) -> None:
        if not 256 <= self.context_budget_tokens <= 1_000_000:
            raise ValueError("context budget must be between 256 and 1000000 tokens")
        if not self.kinds:
            raise ValueError("assembly requires at least one candidate kind")
        if len(self.task_iris) > _MAX_IRIS or len(self.capability_iris) > _MAX_IRIS:
            raise ValueError("assembly accepts at most 32 task and capability IRIs")


@dataclass(frozen=True, slots=True)
class AssemblyOutcome:
    """A proved selection: its record, agent and exposed tool components."""

    record_id: str
    agent_id: str | None
    tool_ids: tuple[str, ...]


def _text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_assembly_request(
    tenant_id: str, requirements: AssemblyRequirements
) -> dict[str, Any]:
    """The ``AssemblyRequest`` wire body for one tenant-bound question."""
    mapped = requirements.task_iris or requirements.capability_iris
    return {
        "tenant_id": tenant_id,
        "requirements": {
            "tasks": list(requirements.task_iris),
            "capabilities": list(requirements.capability_iris),
            "unmapped_task_digests": []
            if mapped
            else [_text_digest(requirements.text)],
            "constraints": {
                "context_budget_tokens": requirements.context_budget_tokens,
                "require_tools": requirements.require_tools,
            },
        },
        "candidates": {"kinds": list(requirements.kinds)},
        "policy": {"policy": "default"},
    }


def _abstain_reasons(outcome: dict[str, Any]) -> list[str]:
    return [
        str(reason.get("reason") or "unknown")
        for reason in outcome.get("reasons") or ()
        if isinstance(reason, dict)
    ]


def parse_assembly_result(payload: Any) -> AssemblyOutcome:
    """Project an ``AssemblyResult``; an abstention raises with its reasons."""
    if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict):
        raise AssemblyUnavailable("AgentAssemble returned an invalid result")
    record = payload["record"]
    record_id = str(record.get("record_id") or "")
    outcome = record.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("outcome") != "solved":
        reasons = _abstain_reasons(outcome if isinstance(outcome, dict) else {})
        raise AssemblyAbstained(record_id, reasons)
    slots = outcome.get("slots") or ()
    tools = tuple(
        str(slot["component"]["component_id"])
        for slot in slots
        if isinstance(slot, dict)
        and isinstance(slot.get("component"), dict)
        and slot["component"].get("kind") == "tool"
    )
    agent = payload.get("agent")
    agent_id = str(agent.get("agent_id")) if isinstance(agent, dict) else None
    return AssemblyOutcome(record_id=record_id, agent_id=agent_id, tool_ids=tools)


def _decoded_payload(result: Any) -> Any:
    """Normalize one generated EG send's result to plain JSON (EH-377a).

    ``send_agent_assemble`` returns a typed ``AssemblyResult`` pydantic model
    (some other generated sends wrap it in an ``OpaqueResult``-like object
    exposing ``.payload``); :func:`parse_assembly_result` reads it as a
    ``Mapping``, so decode once, at this one boundary, rather than at every
    caller.
    """
    payload = getattr(result, "payload", result)
    dump = getattr(payload, "model_dump", None)
    return dump(mode="json") if callable(dump) else payload


def _generated_sender() -> Callable[..., Any] | None:
    from epistemic_graph.generated import storage

    sender = getattr(storage, "send_agent_assemble", None)
    return sender if callable(sender) else None


class AgentAssembly:
    """Ask EG ``AgentAssemble`` under the caller's verified claims."""

    def __init__(self, client_for: Callable[[str], Any]) -> None:
        self._client_for = client_for

    async def assemble(
        self, session: Any, requirements: AssemblyRequirements
    ) -> AssemblyOutcome:
        client = self._client_for(str(session.tenant))
        sender = _generated_sender()
        supports = getattr(client, "supports", None)
        if sender is None or supports is None or await supports(METHOD) is not True:
            raise AssemblyUnavailable(
                "the connected engine does not serve AgentAssemble"
            )
        body = {"request": build_assembly_request(str(session.tenant), requirements)}
        with client.use_verified_context(session.engine_verified_context()):
            result = await sender(client, body, str(session.tenant))
        return parse_assembly_result(_decoded_payload(result))
