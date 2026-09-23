"""Release-pointer promotion and rollback for model-policy versions (EH-347).

The pointer is graph-os state: EG records carry identity only and there is
no pointer record. Every move is an explicit compare-and-swap on the
revision and version the caller last read; a promotion additionally needs the
capability's ``promote`` control (default off) and an accepted held-out
evaluation, and a rollback may only return to the immutable version the
pointer replaced. A failed or cancelled training run cannot reach here: EG
refuses to register a version for it.
"""

from __future__ import annotations

import threading
from typing import Protocol

from .models import (
    ModelPolicyReleasePointer,
    PolicyEvolutionControlError,
    PolicyReleaseMutation,
    release_pointer_digest,
    release_pointer_id,
)
from .records import (
    PolicyRecordReader,
    require_accepted_evaluation,
    require_control,
    require_version,
)

__all__ = [
    "InMemoryReleasePointerRepository",
    "ModelPolicyReleaseService",
    "ReleasePointerRepository",
]


class ReleasePointerRepository(Protocol):
    """Durable owner of the one pointer per tenant/family/channel."""

    def get(self, pointer_id: str) -> ModelPolicyReleasePointer | None: ...

    def compare_and_swap(
        self,
        expected_revision: int,
        expected_version_id: str | None,
        pointer: ModelPolicyReleasePointer,
    ) -> ModelPolicyReleasePointer: ...


class InMemoryReleasePointerRepository:
    """Reference CAS implementation (atomic under one lock)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pointers: dict[str, ModelPolicyReleasePointer] = {}

    def get(self, pointer_id: str) -> ModelPolicyReleasePointer | None:
        with self._lock:
            return self._pointers.get(pointer_id)

    def compare_and_swap(
        self,
        expected_revision: int,
        expected_version_id: str | None,
        pointer: ModelPolicyReleasePointer,
    ) -> ModelPolicyReleasePointer:
        with self._lock:
            current = self._pointers.get(pointer.pointer_id)
            revision = current.revision if current is not None else 0
            version = current.version_id if current is not None else None
            if revision != expected_revision or version != expected_version_id:
                raise PolicyEvolutionControlError("RELEASE_CAS_CONFLICT")
            if pointer.revision != revision + 1:
                raise PolicyEvolutionControlError("RELEASE_REVISION_NOT_MONOTONIC")
            self._pointers[pointer.pointer_id] = pointer
            return pointer


def _next_pointer(
    mutation: PolicyReleaseMutation, current: ModelPolicyReleasePointer | None
) -> ModelPolicyReleasePointer:
    pointer_id = release_pointer_id(
        mutation.tenant_id, mutation.family, mutation.channel
    )
    revision = (current.revision if current is not None else 0) + 1
    previous = current.version_id if current is not None else None
    evaluation = mutation.evaluation_id if mutation.operation == "promote" else None
    return ModelPolicyReleasePointer(
        pointer_id=pointer_id,
        tenant_id=mutation.tenant_id,
        family=mutation.family,
        channel=mutation.channel,
        version_id=mutation.next_version_id,
        revision=revision,
        previous_version_id=previous,
        evaluation_id=evaluation,
        pointer_digest=release_pointer_digest(
            pointer_id, mutation.next_version_id, revision, previous, evaluation
        ),
    )


def _require_expected(
    mutation: PolicyReleaseMutation, current: ModelPolicyReleasePointer | None
) -> None:
    revision = current.revision if current is not None else 0
    version = current.version_id if current is not None else None
    if (
        mutation.expected_revision != revision
        or mutation.expected_version_id != version
    ):
        raise PolicyEvolutionControlError("RELEASE_CAS_CONFLICT")


class ModelPolicyReleaseService:
    """Gate and apply one explicit release-pointer move."""

    def __init__(
        self, records: PolicyRecordReader, repository: ReleasePointerRepository
    ) -> None:
        self._records = records
        self._repository = repository

    def current(
        self, tenant_id: str, family: str, channel: str
    ) -> ModelPolicyReleasePointer | None:
        return self._repository.get(release_pointer_id(tenant_id, family, channel))

    async def apply(self, mutation: PolicyReleaseMutation) -> ModelPolicyReleasePointer:
        """Promote or roll back by CAS; refuse before any state changes."""
        current = self.current(mutation.tenant_id, mutation.family, mutation.channel)
        _require_expected(mutation, current)
        await require_control(
            self._records, mutation.capability_id, "promote", mutation.granted_scopes
        )
        await require_version(self._records, mutation.next_version_id)
        if mutation.operation == "promote":
            await require_accepted_evaluation(
                self._records,
                mutation.evaluation_id or "",
                mutation.next_version_id,
                current.version_id if current is not None else None,
            )
        elif current is None or current.previous_version_id != mutation.next_version_id:
            raise PolicyEvolutionControlError(
                "RELEASE_ROLLBACK_TARGET_INVALID",
                "rollback returns only to the version the pointer replaced",
            )
        return self._repository.compare_and_swap(
            mutation.expected_revision,
            mutation.expected_version_id,
            _next_pointer(mutation, current),
        )
