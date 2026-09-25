"""A2A task projection over AU's public, typed agent control plane.

Every A2A task is one durable EG WorkItem admitted, read, listed and cancelled
through :class:`agent_utilities.api.AgentControlPlane`. GraphOS owns only the
protocol projection: deterministic task/context identities, owner binding,
idempotency conflict detection and the A2A state mapping. It holds no task
store, queue or dispatch logic of its own.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agent_utilities.api import (
    AgentTaskDispatchRequest,
    AgentTaskDispatchResult,
    AgentWorkItemNotCancelable,
    RunOutput,
    RunOutputRequest,
    WorkItemCancelRequest,
    WorkItemGetRequest,
    WorkItemListRequest,
    WorkItemPage,
    WorkItemSnapshot,
)
from agent_utilities.security.persistence_privacy import persistence_reference

from .models import (
    A2AMessage,
    A2AInputMessage,
    A2ARouteDecision,
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
    A2ATextPart,
)

__all__ = [
    "A2AIdempotencyConflict",
    "A2ATaskAuthority",
    "A2ATaskNotCancelable",
    "A2AControlPlanePort",
    "ControlPlaneFactory",
    "WorkItemA2AAuthority",
]


class A2AControlPlanePort(Protocol):
    """The five AU control-plane operations the A2A projection uses.

    ``agent_utilities.api.AgentControlPlane`` satisfies it; the facade depends
    on this port, not on that concrete class, so any conforming plane (a
    hosted one, a test double) can back it.
    """

    async def submit_agent_task(
        self, request: AgentTaskDispatchRequest
    ) -> AgentTaskDispatchResult: ...

    async def get_work_item(
        self, request: WorkItemGetRequest
    ) -> WorkItemSnapshot | None: ...

    async def list_work_items(self, request: WorkItemListRequest) -> WorkItemPage: ...

    async def get_run_output(self, request: RunOutputRequest) -> RunOutput | None: ...

    async def cancel_work_item(
        self, request: WorkItemCancelRequest
    ) -> WorkItemSnapshot | None: ...


ControlPlaneFactory = Callable[[Any], A2AControlPlanePort]

_TASK_PREFIX = "a2a-"
_WORK_ITEM_PREFIX = "workitem:orchestrator:"
_WORK_ITEM_KIND = "orchestrator_task"
_A2A_METADATA_SCHEMA = "graph-os-a2a-unary-v1"
_STATE_MAP: dict[str, A2ATaskState] = {
    "submitted": "submitted",
    "ready": "submitted",
    "leased": "working",
    "running": "working",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "canceled",
    "dead_letter": "failed",
}
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "dead_letter"})


class A2AIdempotencyConflict(RuntimeError):
    """An idempotency key was replayed with a different logical request."""


class A2ATaskNotCancelable(RuntimeError):
    """A task is unknown, terminal, or held by a non-cancelable authority."""


@runtime_checkable
class A2ATaskAuthority(Protocol):
    async def dispatch(
        self,
        *,
        message: A2AMessage,
        idempotency_key: str,
        decision: A2ARouteDecision,
    ) -> A2ATask: ...

    async def get(self, task_id: str) -> A2ATask | None: ...

    async def list(
        self, *, cursor: str | None, limit: int
    ) -> tuple[list[A2ATask], str | None]: ...

    async def cancel(self, task_id: str) -> A2ATask: ...

    async def output(self, task_id: str) -> str | None: ...


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve(scope: str) -> Any:
    from agent_utilities.api import resolve_session

    return resolve_session(required_scope=scope)


def _owner_ref(session: Any) -> str:
    actor_id = str(getattr(session.actor, "actor_id", "") or "").strip()
    if not actor_id:
        raise PermissionError("Verified A2A task owner is unavailable")
    return persistence_reference(
        "actor", actor_id, namespace=f"a2a-owner:{session.tenant}"
    )


def task_id_for(tenant: str, owner_ref: str, key: str) -> str:
    """The deterministic A2A task id for one caller's idempotency key."""
    rendered = key.strip()
    if (
        not rendered
        or len(rendered) > 512
        or any(ord(character) < 32 for character in rendered)
    ):
        raise ValueError("A2A idempotency key is invalid")
    return _TASK_PREFIX + _digest(
        {"tenant": tenant, "owner": owner_ref, "key": rendered}
    )


def work_item_id_for(task_id: str) -> str:
    """The WorkItem id carrying one A2A task; rejects malformed task ids."""
    suffix = task_id.removeprefix(_TASK_PREFIX)
    if (
        not task_id.startswith(_TASK_PREFIX)
        or len(suffix) != 64
        or any(character not in "0123456789abcdef" for character in suffix)
    ):
        raise ValueError("A2A task id is invalid")
    return f"{_WORK_ITEM_PREFIX}{task_id}"


def _owned(item: WorkItemSnapshot | None, owner_ref: str) -> bool:
    metadata = item.metadata if item is not None else {}
    return (
        item is not None
        and item.kind == _WORK_ITEM_KIND
        and metadata.get("a2a_schema") == _A2A_METADATA_SCHEMA
        and metadata.get("a2a_owner_ref") == owner_ref
    )


def project(item: WorkItemSnapshot) -> A2ATask:
    """Project one owned WorkItem snapshot onto the public A2A task shape."""
    state = _STATE_MAP.get(item.status)
    if state is None:
        raise RuntimeError("canonical WorkItem has an invalid A2A state")
    task_id = item.work_item_id.removeprefix(_WORK_ITEM_PREFIX)
    work_item_id_for(task_id)
    route = item.metadata.get("a2a_route")
    from agent_utilities.observability.trace_ontology import trace_id

    timestamp = datetime.fromtimestamp(item.updated_at_ms / 1000, tz=UTC)
    input_message = _pending_plan_message(item)
    if input_message is not None:
        state = "input-required"
    return A2ATask(
        id=task_id,
        context_id=str(item.metadata.get("a2a_context_id") or ""),
        status=A2ATaskStatus(
            state=state, timestamp=timestamp.isoformat(), message=input_message
        ),
        metadata={
            "graphOs": {
                "taskAuthorityRef": item.work_item_id,
                "runId": task_id,
                "routing": route if isinstance(route, dict) else {},
                "runTraceRef": trace_id(task_id),
            }
        },
    )


def _pending_plan_message(item: WorkItemSnapshot) -> A2AInputMessage | None:
    """Expose only a live, well-formed approval marker from a running WorkItem."""

    if item.status not in {"leased", "running"}:
        return None
    marker = item.metadata.get("pending_input_request")
    if not isinstance(marker, dict) or marker.get("kind") != "graphos.plan":
        return None
    plan_ref = marker.get("plan_ref")
    op = marker.get("op")
    digest = marker.get("params_digest")
    expiry = marker.get("expires_at_ms")
    if (
        not isinstance(plan_ref, str)
        or re.fullmatch(r"graphos_plan:[0-9a-f]{48}", plan_ref) is None
        or not isinstance(op, str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", op)
        is None
        or len(op) > 256
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or isinstance(expiry, bool)
        or not isinstance(expiry, int)
        or expiry <= int(time.time() * 1000)
    ):
        return None
    preview = marker.get("preview")
    safe_preview: dict[str, str] = {}
    if isinstance(preview, dict):
        effect = preview.get("effect")
        summary = preview.get("summary")
        if effect in {"read", "write", "destructive", "admin"}:
            safe_preview["effect"] = effect
        if isinstance(summary, str) and len(summary) <= 256:
            safe_preview["summary"] = summary
    return A2AInputMessage(
        parts=[A2ATextPart(text="Tool confirmation required")],
        message_id=plan_ref,
        metadata={
            "graphOsPlan": {
                "plan_ref": plan_ref,
                "op": op,
                "params_digest": digest,
                "expires_at_ms": expiry,
                "preview": safe_preview,
                "confirm": "plan",
            }
        },
    )


class WorkItemA2AAuthority:
    """A2A lifecycle over the verified caller's AU control plane."""

    def __init__(self, control_plane_for: ControlPlaneFactory) -> None:
        self._control_plane_for = control_plane_for

    def _bound(self, scope: str) -> tuple[Any, A2AControlPlanePort, str]:
        session = _resolve(scope)
        return session, self._control_plane_for(session), _owner_ref(session)

    async def dispatch(
        self,
        *,
        message: A2AMessage,
        idempotency_key: str,
        decision: A2ARouteDecision,
    ) -> A2ATask:
        session, control_plane, owner_ref = self._bound("kg:write")
        task_id = task_id_for(session.tenant, owner_ref, idempotency_key)
        context_id = "a2a-context-" + _digest(
            {
                "tenant": session.tenant,
                "owner": owner_ref,
                "context": message.context_id or task_id,
            }
        )
        request_digest = _digest(
            {
                "context_id": context_id,
                "decision": decision.model_dump(mode="json"),
                "message": message.model_dump(mode="json", by_alias=True),
            }
        )
        result = await control_plane.submit_agent_task(
            AgentTaskDispatchRequest(
                work_item_id=work_item_id_for(task_id),
                idempotency_key=task_id,
                job_id=task_id,
                session_ref=context_id,
                task=message.task_text(),
                agent_name=decision.agent_name,
                # The signed dispatch carrier binds and the worker enforces
                # the assembled subset; None keeps the agent's own tools.
                allowed_tools=decision.selected_tools or None,
                task_iri=decision.task_iri,
                metadata={
                    "a2a_schema": _A2A_METADATA_SCHEMA,
                    "a2a_context_id": context_id,
                    "a2a_message_ref": persistence_reference(
                        "message", message.message_id, namespace="graph-os-a2a"
                    ),
                    "a2a_owner_ref": owner_ref,
                    "a2a_request_digest": request_digest,
                    "a2a_route": decision.model_dump(mode="json", by_alias=True),
                },
            )
        )
        item = result.admission.item
        if not _owned(item, owner_ref):
            raise A2AIdempotencyConflict("A2A idempotency authority conflicts")
        if item.metadata.get("a2a_request_digest") != request_digest:
            raise A2AIdempotencyConflict(
                "A2A idempotency key was reused with a different request"
            )
        return project(item)

    async def _owned_item(
        self, control_plane: A2AControlPlanePort, task_id: str, owner_ref: str
    ) -> WorkItemSnapshot | None:
        item = await control_plane.get_work_item(
            WorkItemGetRequest(work_item_id=work_item_id_for(task_id))
        )
        return item if _owned(item, owner_ref) else None

    async def get(self, task_id: str) -> A2ATask | None:
        _session, control_plane, owner_ref = self._bound("kg:read")
        item = await self._owned_item(control_plane, task_id, owner_ref)
        return None if item is None else project(item)

    async def list(
        self, *, cursor: str | None, limit: int
    ) -> tuple[list[A2ATask], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("A2A task page limit must be between 1 and 100")
        session, control_plane, owner_ref = self._bound("kg:read")
        page = await control_plane.list_work_items(
            WorkItemListRequest(
                cursor=_decode_cursor(cursor, session.tenant, owner_ref),
                limit=limit,
                kind=_WORK_ITEM_KIND,
            )
        )
        tasks = [project(item) for item in page.items if _owned(item, owner_ref)]
        return tasks, _encode_cursor(page.next_cursor, session.tenant, owner_ref)

    async def output(self, task_id: str) -> str | None:
        """The owned task's final answer text (AU run output), if any."""
        _session, control_plane, owner_ref = self._bound("kg:read")
        if await self._owned_item(control_plane, task_id, owner_ref) is None:
            return None
        result = await control_plane.get_run_output(RunOutputRequest(run_id=task_id))
        if result is None or result.status != "succeeded" or not result.output:
            return None
        return result.output

    async def cancel(self, task_id: str) -> A2ATask:
        _session, control_plane, owner_ref = self._bound("kg:write")
        item = await self._owned_item(control_plane, task_id, owner_ref)
        if item is None:
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        if item.status == "cancelled":
            return project(item)
        if item.status in _TERMINAL:
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        try:
            latest = await control_plane.cancel_work_item(
                WorkItemCancelRequest(work_item_id=item.work_item_id)
            )
        except AgentWorkItemNotCancelable as exc:
            raise A2ATaskNotCancelable("A2A task is not cancelable") from exc
        if latest is None:
            raise RuntimeError("A2A task disappeared after cancellation")
        return project(latest)


def _encode_cursor(cursor: str | None, tenant: str, owner_ref: str) -> str | None:
    """Bind the authority's opaque cursor to this caller's tenant and owner."""
    if cursor is None:
        return None
    return f"{_digest({'tenant': tenant, 'owner': owner_ref, 'c': cursor})}.{cursor}"


def _decode_cursor(cursor: str | None, tenant: str, owner_ref: str) -> str | None:
    if cursor is None:
        return None
    digest, separator, inner = cursor.partition(".")
    if not separator or digest != _digest(
        {"tenant": tenant, "owner": owner_ref, "c": inner}
    ):
        raise ValueError("A2A task cursor is invalid")
    return inner
