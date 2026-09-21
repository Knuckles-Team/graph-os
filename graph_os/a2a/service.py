"""Application service for the authenticated unary A2A boundary."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fasta2a.schema import Message, Task

from .persistence import EpistemicGraphA2ABroker, EpistemicGraphA2AStorage

__all__ = ["A2AIdempotencyConflict", "A2AService"]

_IDEMPOTENCY_KIND = "a2a_request_idempotency_current"
_IDEMPOTENCY_FIELDS = frozenset(
    {"record_kind", "node_type", "tenant_ref", "request_digest", "task_id"}
)


class A2AIdempotencyConflict(RuntimeError):
    """An idempotency key was reused for a different logical request."""


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class A2AService:
    storage: EpistemicGraphA2AStorage
    broker: EpistemicGraphA2ABroker

    def _idempotency_id(self, key: str) -> str:
        rendered = key.strip()
        if (
            not rendered
            or len(rendered) > 512
            or any(ord(char) < 32 for char in rendered)
        ):
            raise ValueError("A2A idempotency key is invalid")
        key_digest = _digest(
            {"tenant": self.storage.runtime.tenant_key, "key": rendered}
        )
        return f"a2a.idempotency.{self.storage.runtime.tenant_key}.{key_digest}"

    def _request_record(self, *, request_digest: str, task_id: str) -> dict[str, str]:
        return {
            "record_kind": _IDEMPOTENCY_KIND,
            "node_type": "A2ARequestIdempotency",
            "tenant_ref": self.storage.runtime.tenant_ref,
            "request_digest": request_digest,
            "task_id": task_id,
        }

    async def _replay(self, record_id: str, request_digest: str) -> Task:
        existing = await self.storage.runtime.call("nodes", "properties", record_id)
        if not isinstance(existing, dict) or set(existing) != _IDEMPOTENCY_FIELDS:
            raise RuntimeError("native A2A idempotency record is invalid")
        valid_authority = (
            existing.get("record_kind") == _IDEMPOTENCY_KIND
            and existing.get("tenant_ref") == self.storage.runtime.tenant_ref
        )
        if not valid_authority:
            raise RuntimeError("native A2A idempotency record is invalid")
        if existing.get("request_digest") != request_digest:
            raise A2AIdempotencyConflict(
                "A2A idempotency key was reused with a different request"
            )
        replay = await self.storage.load_task(str(existing.get("task_id") or ""))
        if replay is None:
            raise RuntimeError("A2A idempotent request is still being committed")
        return replay

    async def send_message(
        self, *, message: Message, context_id: str | None, idempotency_key: str
    ) -> Task:
        await self.storage.runtime.start()
        request = {"message": message, "context_id": context_id or ""}
        request_digest = _digest(request)
        record_id = self._idempotency_id(idempotency_key)
        task_id = f"{self.storage.runtime.task_prefix()}{uuid.uuid4().hex}"
        record = self._request_record(request_digest=request_digest, task_id=task_id)
        created = await self.storage.runtime.call(
            "nodes", "create_if_absent", record_id, record
        )
        if not isinstance(created, bool):
            raise RuntimeError(
                "native A2A idempotency reservation returned invalid data"
            )
        if not created:
            return await self._replay(record_id, request_digest)

        # The idempotency record owns the task identity before task creation, closing
        # the duplicate-create race. Storage remains the sole task authority.
        task = await self.storage.create_task(
            context_id or uuid.uuid4().hex, message, task_id=task_id
        )
        await self.broker.run_task(
            {"id": task["id"], "context_id": task["context_id"], "message": message}
        )
        return task

    async def get_task(self, task_id: str) -> Task | None:
        return await self.storage.load_task(task_id)

    async def list_tasks(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[list[Task], str | None]:
        return await self.storage.list_tasks(after=cursor, limit=limit)

    async def cancel_task(self, task_id: str) -> Task:
        await self.broker.cancel_task({"id": task_id})
        task = await self.storage.load_task(task_id)
        if task is None:
            raise ValueError("A2A task is unavailable")
        return task
