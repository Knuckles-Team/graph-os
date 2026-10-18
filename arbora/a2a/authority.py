"""Canonical WorkItem/RunTrace authority used by every unary A2A surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agent_connector_sdk.privacy import persistence_reference

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
    "A2AStreamingUnavailable",
    "A2ATransitionHistoryUnavailable",
    "A2ATaskAuthority",
    "A2ATaskNotCancelable",
    "WorkItemA2AAuthority",
]

_TASK_PREFIX = "a2a-"
_WORK_ITEM_PREFIX = "workitem:orchestrator:"
_A2A_METADATA_SCHEMA = "arbora-a2a-unary-v1"
#: How long one agent run may hold its WorkItem lease.
_RUN_LEASE_SECONDS = 600.0
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


class A2AStreamingUnavailable(RuntimeError):
    """Durable task streaming/resubscribe has no event-cursor backing yet.

    ARBORA-A2A-R001 adds durable task streaming and resubscribe without a
    second task store. That bounded, restart-safe event-cursor backing does
    not exist yet, so ``tasks/resubscribe`` must fail closed rather than
    silently succeed or be reported as an unrecognized method.
    """


class A2ATransitionHistoryUnavailable(RuntimeError):
    """No reachable durable transition-history authority backs ``tasks/get`` history.

    ARBORA-A2A-R012.2: when no ``A2ATransitionHistoryAuthority`` is wired or
    the wired one is unreachable, history requests fail closed with this
    distinct error rather than an empty or partially reconstructed history.
    """

    def __init__(
        self, message: str = "durable task transition history is not available"
    ) -> None:
        super().__init__(message)


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

    def __init__(
        self, engine_provider: Callable[[], Any], *, executor: Any = None
    ) -> None:
        self._engine_provider = engine_provider
        self._executor = executor

    @staticmethod
    def _session(scope: str) -> Any:
        from arbora.api.contracts.session import resolve_session

        return resolve_session(required_scope=scope)

    def _engine(self) -> tuple[Any, Any]:
        engine = self._engine_provider()
        if engine is None:
            raise RuntimeError("Arbora task authority is unavailable")
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
                "message", message.message_id, namespace="arbora-a2a"
            ),
            "a2a_owner_ref": owner_ref,
            "a2a_request_digest": request_digest,
            "a2a_route": decision.model_dump(mode="json", by_alias=True),
        }

    @staticmethod
    def _owned(item: Any, owner_ref: str) -> bool:
        """The engine scopes ``get`` to the verified tenant; the owner is in metadata."""
        metadata = item.get("metadata") if isinstance(item, dict) else None
        return (
            isinstance(item, dict)
            and item.get("kind") == "orchestrator_task"
            and isinstance(metadata, dict)
            and metadata.get("a2a_schema") == _A2A_METADATA_SCHEMA
            and metadata.get("a2a_owner_ref") == owner_ref
        )

    @classmethod
    def _validate_existing(
        cls, item: dict[str, Any], *, session: Any, owner_ref: str, request_digest: str
    ) -> None:
        del session
        if not cls._owned(item, owner_ref):
            raise A2AIdempotencyConflict("A2A idempotency authority conflicts")
        if item["metadata"].get("a2a_request_digest") != request_digest:
            raise A2AIdempotencyConflict(
                "A2A idempotency key was reused with a different request"
            )

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
        from arbora import work_items

        return work_items.submit_work_item_atomic(
            work_engine,
            kind="orchestrator_task",
            queue="orchestrator_task",
            payload_ref=task_id,
            tenant=session.tenant,
            description=description,
            resource_class="agent_dispatch",
            work_item_id=f"{_WORK_ITEM_PREFIX}{task_id}",
            idempotency_key=task_id,
            created_by=owner_ref,
            metadata=metadata,
        )

    def _agent_executor(self) -> Any:
        if self._executor is None:
            from arbora.agent_runs import AgentRunPort

            self._executor = AgentRunPort()
        return self._executor

    async def _run(
        self,
        work_engine: Any,
        *,
        item_id: str,
        session: Any,
        agent_name: str,
        task: str,
    ) -> None:
        """Lease the WorkItem, run the agent through the harness, commit the outcome."""
        from arbora import work_items
        from arbora.api.contracts.agent_control_contracts import (
            AgentExecutionRequest,
        )

        claim = await asyncio.to_thread(
            work_items.claim_specific,
            work_engine,
            item_id,
            token=f"arbora-a2a:{item_id}",
            lease_ttl_s=_RUN_LEASE_SECONDS,
        )
        if claim is None:
            return
        outcome, result_ref, error_ref = "succeeded", None, None
        try:
            result = await self._agent_executor().execute_agent(
                AgentExecutionRequest(agent_name=agent_name, task=task),
                session=session,
            )
            result_ref = persistence_reference(
                "run", result.run_id, namespace="arbora-a2a"
            )
        except Exception as exc:  # noqa: BLE001 - a failed run is a failed task
            outcome = "failed"
            error_ref = persistence_reference(
                "error", type(exc).__name__, namespace="arbora-a2a"
            )
        await asyncio.to_thread(
            work_items.commit_result,
            work_engine,
            item_id,
            claim,
            outcome=outcome,
            result_ref=result_ref,
            error_ref=error_ref,
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
        _engine, work_engine = self._engine()
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
        from arbora.access.task_guard import screen_and_redact_agent_task

        description = screen_and_redact_agent_task(message.task_text())
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

        from arbora import work_items

        item = await asyncio.to_thread(work_items.get_work_item, work_engine, item_id)
        if not isinstance(item, dict):
            raise RuntimeError("canonical A2A WorkItem is unavailable after admission")
        self._validate_existing(
            item,
            session=session,
            owner_ref=owner_ref,
            request_digest=request_digest,
        )
        if created or item.get("status") not in _TERMINAL:
            await self._run(
                work_engine,
                item_id=item_id,
                session=session,
                agent_name=decision.agent_name,
                task=description,
            )
            refreshed = await asyncio.to_thread(
                work_items.get_work_item, work_engine, item_id
            )
            if isinstance(refreshed, dict):
                item = refreshed
        return self._project(item, task_id=task_id)

    def _authorized_item(self, task_id: str, scope: str) -> dict[str, Any] | None:
        from arbora import work_items

        session = self._session(scope)
        _engine, work_engine = self._engine()
        item = work_items.get_work_item(work_engine, self._work_item_id(task_id))
        return item if self._owned(item, self._owner_ref(session)) else None

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
        from arbora.observability.trace_ontology import (
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
            metadata={"arbora": public_metadata},
        )

    async def get_committed_task(self, task_id: str) -> A2ATask | None:
        return await self.get(task_id, include_committed_event=True)

    async def get(
        self, task_id: str, *, include_committed_event: bool = False
    ) -> A2ATask | None:
        original = self._session("work:read") if include_committed_event else None
        item = await asyncio.to_thread(self._authorized_item, task_id, "kg:read")
        if item is None:
            return None
        if original is not None:
            current = self._session("work:read")
            owner_ref = self._owner_ref(original)
            if (
                current != original
                or item.get("tenant") != original.tenant
                or item.get("created_by") != owner_ref
                or item.get("metadata", {}).get("a2a_owner_ref") != owner_ref
            ):
                raise PermissionError("A2A caller changed during task read")
        task = self._project(item, task_id=task_id)
        if include_committed_event:
            from .terminal_event import (
                A2ATerminalEventUnavailable,
                read_terminal_event,
            )

            session = original
            owner_ref = self._owner_ref(session)
            engine, _work_engine = self._engine()
            try:
                event = await asyncio.wait_for(
                    read_terminal_event(
                        engine, session=session, task_id=task_id, owner_ref=owner_ref
                    ),
                    timeout=5.0,
                )
            except TimeoutError as exc:
                raise A2ATerminalEventUnavailable(
                    "committed terminal event is unavailable"
                ) from exc
            current = self._session("work:read")
            if (
                current != session
                or current.tenant != session.tenant
                or current.graph != session.graph
                or self._owner_ref(current) != owner_ref
            ):
                raise PermissionError("A2A caller changed during terminal read")
            if event["status"] == "available" and (
                _STATE_MAP[event["outcome"]] != task.status.state
            ):
                raise A2ATerminalEventUnavailable(
                    "committed terminal event is unavailable"
                )
            task.metadata["arbora"]["terminalEvent"] = event
        return task

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
        from arbora import work_items

        _engine, work_engine = self._engine()
        item_id = self._work_item_id(task_id)
        if not await asyncio.to_thread(
            work_items.cancel_work_item, work_engine, item_id
        ):
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        current = await asyncio.to_thread(
            work_items.get_work_item, work_engine, item_id
        )
        if not isinstance(current, dict):
            raise A2ATaskNotCancelable("A2A task is not cancelable")
        return self._project(current, task_id=task_id)
