"""The model-policy release pointer as an EG-committed node with engine CAS.

The pointer is graph-os state, but it is durable in EG, not in this process:
one node per tenant/family/channel whose id is the pointer id. The first
move is ``create_if_absent`` (a concurrent first writer loses); every later
move is ``compare_and_set`` conditioned on the revision and version the
caller last read, executed atomically by the engine. Reads re-validate the
stored pointer (content-addressed id and digest) before trusting it.
"""

from __future__ import annotations

from typing import Any

from .models import ModelPolicyReleasePointer, PolicyEvolutionControlError

__all__ = ["EgReleasePointerRepository"]

_KIND = "model_policy_release_pointer"


def _node(pointer: ModelPolicyReleasePointer) -> dict[str, Any]:
    return {"kind": _KIND, **pointer.model_dump(mode="python")}


class EgReleasePointerRepository:
    """A :class:`ReleasePointerRepository` over EG node CAS."""

    def __init__(self, client: Any) -> None:
        self._nodes = client.nodes

    async def get(self, pointer_id: str) -> ModelPolicyReleasePointer | None:
        properties = await self._nodes.properties(pointer_id)
        if properties is None:
            return None
        body = dict(properties)
        if body.pop("kind", None) != _KIND:
            raise PolicyEvolutionControlError(
                "RELEASE_POINTER_FOREIGN_NODE", pointer_id
            )
        try:
            return ModelPolicyReleasePointer.model_validate(body)
        except ValueError as invalid:
            raise PolicyEvolutionControlError(
                "RELEASE_POINTER_TAMPERED", pointer_id
            ) from invalid

    async def compare_and_swap(
        self,
        expected_revision: int,
        expected_version_id: str | None,
        pointer: ModelPolicyReleasePointer,
    ) -> ModelPolicyReleasePointer:
        if pointer.revision != expected_revision + 1:
            raise PolicyEvolutionControlError("RELEASE_REVISION_NOT_MONOTONIC")
        if expected_revision == 0:
            applied = await self._nodes.create_if_absent(
                pointer.pointer_id, _node(pointer)
            )
        else:
            applied = await self._nodes.compare_and_set(
                pointer.pointer_id,
                {
                    "kind": _KIND,
                    "revision": expected_revision,
                    "version_id": expected_version_id,
                },
                _node(pointer),
            )
        if applied is not True:
            raise PolicyEvolutionControlError("RELEASE_CAS_CONFLICT")
        return pointer
