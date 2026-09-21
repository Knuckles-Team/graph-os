"""Canonical WorkItem/RunTrace authority used by every unary A2A surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agent_utilities.security.persistence_privacy import persistence_reference

from .models import (
    A2AMessage,
    A2ARouteDecision,
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
)
from .routing import A2AAssemblyUnavailable

__all__ = [
    "A2AIdempotencyConflict",
    "A2ATaskAuthority",
    "A2ATaskNotCancelable",
    "WorkItemA2AAuthority",
]

_TASK_PREFIX = "a2a-"
_WORK_ITEM_PREFIX = "workitem:orchestrator:"
_A2A_METADATA_SCHEMA = "graph-os-a2a-unary-v1"
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "dead_letter"})
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


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class WorkItemA2AAuthority:
    """A2A projection over AU's sole durable WorkItem and dispatch authorities."""

    def __init__(self, engine_provider: Callable[[], Any]) -> None:
        self._engine_provider = engine_provider

    @staticmethod
    def _session(scope: str) -> Any:
        from agent_utilities.knowledge_graph.core.session import resolve_session

        return resolve_session(required_scope=scope)

    def _engine(self) -> tuple[Any, Any]:
        engine = self._engine_provider()
        if engine is None:
            raise RuntimeError("GraphOS task authority is unavailable")
        return engine, getattr(engine, "_work_item_engine", engine)

    @staticmethod
    def _owner_ref(session: Any) -> str:
        actor_id = str(getattr(session.actor, "actor_id", "") or "").strip()
        if not actor_id:
            raise PermissionError("Verified A2A task owner is unavailable")
        return persistence_reference(
            "actor", actor_id, namespace=f"a2a-owner:{session.tenant}"
        )

    @staticmethod
    def _task_id(tenant: str, owner_ref: str, key: str) -> str:
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

    @staticmethod
    def _work_item_id(task_id: str) -> str:
        suffix = task_id.removeprefix(_TASK_PREFIX)
        if not task_id.startswith(_TASK_PREFIX) or len(suffix) != 64:
            raise ValueError("A2A task id is invalid")
        if any(character not in "0123456789abcdef" for character in suffix):
            raise ValueError("A2A task id is invalid")
        return f"{_WORK_ITEM_PREFIX}{task_id}"

    @staticmethod
    def _context_id(
        session: Any, message: A2AMessage, task_id: str, owner_ref: str
    ) -> str:
        supplied = message.context_id or task_id
        digest = _digest(
            {"tenant": session.tenant, "owner": owner_ref, "context": supplied}
        )
        return f"a2a-context-{digest}"

    @staticmethod
    def _request_digest(
        message: A2AMessage, context_id: str, decision: A2ARouteDecision
    ) -> str:
        return _digest(
            {
                "context_id": context_id,
                "decision": decision.model_dump(mode="json"),
                "message": message.model_dump(mode="json", by_alias=True),
            }
        )

    @staticmethod
    def _metadata(
        *,
        message: A2AMessage,
        context_id: str,
        owner_ref: str,
        request_digest: str,
        decision: A2ARouteDecision,
    ) -> dict[str, Any]:
        return {
            "a2a_schema": _A2A_METADATA_SCHEMA,
            "a2a_context_id": context_id,
            "a2a_message_ref": persistence_reference(
                "message", message.message_id, namespace="graph-os-a2a"
            ),
            "a2a_owner_ref": owner_ref,
            "a2a_request_digest": request_digest,
            "a2a_route": decision.model_dump(mode="json", by_alias=True),
        }

    @staticmethod
    def _validate_existing(
        item: dict[str, Any], *, session: Any, owner_ref: str, request_digest: str
    ) -> None:
        metadata = item.get("metadata")
        valid = (
            item.get("kind") == "orchestrator_task"
            and item.get("tenant") == session.tenant
            and item.get("created_by") == owner_ref
            and isinstance(metadata, dict)
            and metadata.get("a2a_schema") == _A2A_METADATA_SCHEMA
            and metadata.get("a2a_owner_ref") == owner_ref
        )
        if not valid:
            raise A2AIdempotencyConflict("A2A idempotency authority conflicts")
        assert isinstance(metadata, dict)
        if metadata.get("a2a_request_digest") != request_digest:
            raise A2AIdempotencyConflict(
                "A2A idempotency key was reused with a different request"
            )

    @staticmethod
    def _prepare_task(engine: Any, text: str) -> str:
        from agent_utilities.orchestration.manager import Orchestrator

        orchestrator = Orchestrator(engine)
        orchestrator._scan_task(text)
        return orchestrator._redact_task(text)

    @staticmethod
    def _submit(
        work_engine: Any,
        *,
        task_id: str,
        session: Any,
        owner_ref: str,
        description: str,
        metadata: dict[str, Any],
    ) -> tuple[str, bool]:
        from agent_utilities.knowledge_graph.core import work_durability as work

        return work.submit_work_item_atomic(
            work_engine,
            kind="orchestrator_task",
            queue="orchestrator_task",
            payload_ref=task_id,
            tenant=session.tenant,
            description=description,
            resource_class="agent_dispatch",
            work_item_id=work.orchestrator_work_item_id(task_id),
            idempotency_key=task_id,
            created_by=owner_ref,
            metadata=metadata,
        )

    @staticmethod
    def _enqueue(
        engine: Any,
        *,
        task_id: str,
        context_id: str,
        tenant: str,
        decision: A2ARouteDecision,
    ) -> None:
        # AU's current carrier cannot enforce an assembly-selected tool subset.
        # Refuse instead of persisting an advisory list that execution ignores.
        if decision.selected_tools:
            raise A2AAssemblyUnavailable(
                "canonical agent dispatch cannot yet enforce an assembled tool subset"
            )
        from agent_utilities.orchestration.agent_dispatch import (
            KIND_ORCHESTRATOR_TASK,
            AgentTurnEnvelope,
            enqueue_agent_turn,
        )

        enqueue_agent_turn(
            AgentTurnEnvelope(
                job_id=task_id,
                session_id=context_id,
                kind=KIND_ORCHESTRATOR_TASK,
                payload_ref=task_id,
                agent_name=decision.agent_name,
                tenant=tenant,
            ),
            engine=engine,
        )

    async def dispatch(
        self,
        *,
        message: A2AMessage,
        idempotency_key: str,
        decision: A2ARouteDecision,
    ) -> A2ATask:
        if decision.selected_tools:
            # Refuse before durable admission: the current signed AU dispatch
            # carrier cannot enforce this allowlist at execution time.
            raise A2AAssemblyUnavailable(
                "canonical agent dispatch cannot yet enforce an assembled tool subset"
            )
        session = self._session("kg:write")
        engine, work_engine = self._engine()
        owner_ref = self._owner_ref(session)
        task_id = self._task_id(session.tenant, owner_ref, idempotency_key)
        context_id = self._context_id(session, message, task_id, owner_ref)
        request_digest = self._request_digest(message, context_id, decision)
        metadata = self._metadata(
            message=message,
            context_id=context_id,
            owner_ref=owner_ref,
            request_digest=request_digest,
            decision=decision,
        )
        description = self._prepare_task(engine, message.task_text())
        item_id, created = await asyncio.to_thread(
            self._submit,
            work_engine,
            task_id=task_id,
            session=session,
            owner_ref=owner_ref,
            description=description,
            metadata=metadata,
        )
        if item_id != self._work_item_id(task_id):
            raise RuntimeError("canonical A2A WorkItem returned an invalid identity")

        from agent_utilities.knowledge_graph.core import work_durability as work

        item = await asyncio.to_thread(work.get_work_item, work_engine, item_id)
        if not isinstance(item, dict):
            raise RuntimeError("canonical A2A WorkItem is unavailable after admission")
        self._validate_existing(
            item,
            session=session,
            owner_ref=owner_ref,
            request_digest=request_digest,
        )
        if created or item.get("status") not in _TERMINAL:
            await asyncio.to_thread(
                self._enqueue,
                engine,
                task_id=task_id,
                context_id=context_id,
                tenant=session.tenant,
                decision=decision,
            )
        return self._project(item, task_id=task_id)

    def _authorized_item(self, task_id: str, scope: str) -> dict[str, Any] | None:
        session = self._session(scope)
        _engine, work_engine = self._engine()
        from agent_utilities.knowledge_graph.core import work_durability as work

        item = work.get_work_item(work_engine, self._work_item_id(task_id))
        owner_ref = self._owner_ref(session)
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not (
            isinstance(item, dict)
            and item.get("tenant") == session.tenant
            and item.get("created_by") == owner_ref
            and isinstance(metadata, dict)
            and metadata.get("a2a_schema") == _A2A_METADATA_SCHEMA
            and metadata.get("a2a_owner_ref") == owner_ref
        ):
            return None
        return item

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        rendered = str(value).strip()
        return rendered or None

    @staticmethod
    def _project(item: dict[str, Any], *, task_id: str) -> A2ATask:
        raw_state = str(item.get("status") or "")
        state = _STATE_MAP.get(raw_state)
        if state is None:
            raise RuntimeError("canonical WorkItem has an invalid A2A state")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("canonical WorkItem lacks A2A metadata")
        route = metadata.get("a2a_route")
        public_route = route if isinstance(route, dict) else {}
        public_metadata: dict[str, Any] = {
            "taskAuthorityRef": f"{_WORK_ITEM_PREFIX}{task_id}",
            "runId": task_id,
            "routing": public_route,
        }
        from agent_utilities.observability.trace_ontology import (
            trace_id as canonical_trace_id,
        )

        public_metadata["runTraceRef"] = canonical_trace_id(task_id)
        return A2ATask(
            id=task_id,
            context_id=str(metadata.get("a2a_context_id") or ""),
            status=A2ATaskStatus(
                state=state,
                timestamp=WorkItemA2AAuthority._timestamp(item.get("updated_at")),
            ),
            metadata={"graphOs": public_metadata},
        )

    async def get(self, task_id: str) -> A2ATask | None:
        item = await asyncio.to_thread(self._authorized_item, task_id, "kg:read")
        return None if item is None else self._project(item, task_id=task_id)

    @staticmethod
    def _cursor(owner_ref: str, tenant: str, item_id: str) -> str:
        return f"{_digest({'tenant': tenant, 'owner': owner_ref, 'id': item_id})}.{item_id}"

    @classmethod
    def _decode_cursor(cls, cursor: str | None, *, owner_ref: str, tenant: str) -> str:
        if cursor is None:
            return ""
        digest, separator, item_id = cursor.partition(".")
        if not separator or digest != _digest(
            {"tenant": tenant, "owner": owner_ref, "id": item_id}
        ):
            raise ValueError("A2A task cursor is invalid")
        cls._work_item_id(item_id.removeprefix(_WORK_ITEM_PREFIX))
        return item_id

    def _list_rows(self, *, after: str, limit: int, tenant: str, owner_ref: str) -> Any:
        _engine, work_engine = self._engine()
        return work_engine.query_cypher(
            "MATCH (w:WorkItem) "
            "WHERE w.kind = $kind AND w.tenant = $tenant "
            "AND w.created_by = $owner AND w.id > $after "
            "AND w.id STARTS WITH $id_prefix "
            "RETURN w.id AS id, w.status AS status, w.updated_at AS updated_at, "
            "w.metadata AS metadata, w.tenant AS tenant, w.created_by AS created_by "
            "ORDER BY w.id ASC LIMIT $limit",
            {
                "kind": "orchestrator_task",
                "tenant": tenant,
                "owner": owner_ref,
                "after": after,
                "id_prefix": f"{_WORK_ITEM_PREFIX}{_TASK_PREFIX}",
                "limit": limit + 1,
            },
        )

    @staticmethod
    def _validated_list_rows(rows: Any, limit: int) -> list[Any]:
        if not isinstance(rows, list) or len(rows) > limit + 1:
            raise RuntimeError("canonical WorkItem list returned invalid data")
        return rows

    @classmethod
    def _project_list_row(cls, row: Any, *, tenant: str, owner_ref: str) -> A2ATask:
        if not isinstance(row, dict):
            raise RuntimeError("canonical WorkItem list returned an invalid row")
        metadata = row.get("metadata")
        authorized = (
            row.get("tenant") == tenant
            and row.get("created_by") == owner_ref
            and isinstance(metadata, dict)
            and metadata.get("a2a_schema") == _A2A_METADATA_SCHEMA
            and metadata.get("a2a_owner_ref") == owner_ref
        )
        if not authorized:
            raise RuntimeError("canonical WorkItem list crossed its authority scope")
        item_id = str(row.get("id") or "")
        task_id = item_id.removeprefix(_WORK_ITEM_PREFIX)
        cls._work_item_id(task_id)
        return cls._project(row, task_id=task_id)

    @classmethod
    def _next_cursor(
        cls,
        rows: list[Any],
        tasks: list[A2ATask],
        *,
        limit: int,
        owner_ref: str,
        tenant: str,
    ) -> str | None:
        if len(rows) <= limit or not tasks:
            return None
        item_id = str(rows[limit - 1].get("id") or "")
        return cls._cursor(owner_ref, tenant, item_id)

    async def list(
        self, *, cursor: str | None, limit: int
    ) -> tuple[list[A2ATask], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("A2A task page limit must be between 1 and 100")
        session = self._session("kg:read")
        owner_ref = self._owner_ref(session)
        after = self._decode_cursor(cursor, owner_ref=owner_ref, tenant=session.tenant)
        rows = await asyncio.to_thread(
            self._list_rows,
            after=after,
            limit=limit,
            tenant=session.tenant,
            owner_ref=owner_ref,
        )
        page = self._validated_list_rows(rows, limit)
        tasks = [
            self._project_list_row(
                row,
                tenant=session.tenant,
                owner_ref=owner_ref,
            )
            for row in page[:limit]
        ]
        return tasks, self._next_cursor(
            page,
            tasks,
            limit=limit,
            owner_ref=owner_ref,
            tenant=session.tenant,
        )

    async def cancel(self, task_id: str) -> A2ATask:
        item = await asyncio.to_thread(self._authorized_item, task_id, "kg:write")
        if item is None:
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        if item.get("status") == "cancelled":
            return self._project(item, task_id=task_id)
        if item.get("status") in _TERMINAL:
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        _engine, work_engine = self._engine()
        from agent_utilities.knowledge_graph.core import work_durability as work

        cancelled = await asyncio.to_thread(
            work.cancel_work_item,
            work_engine,
            self._work_item_id(task_id),
            reason="a2a_client_cancelled",
        )
        if not cancelled:
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        latest = await asyncio.to_thread(self._authorized_item, task_id, "kg:read")
        if latest is None:
            raise RuntimeError("A2A task disappeared after cancellation")
        return self._project(latest, task_id=task_id)
