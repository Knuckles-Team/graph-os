"""D18 write-back: a typed, idempotent, previewed registry operation.

GRAPHOS-FLEET-R003 (.1 slice): the typed preview model and its refusal rule —
D18 write-back must never execute without a prior preview and an idempotency
key, so it can never be reached as an unguarded native child tool. Wiring a
live handler/binding into the operation registry is a further slice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import Confirm, Idempotency

#: The registry binding this preview is written for once wired into the
#: registry (GRAPHOS-FLEET-R003 follow-up): always previewed, always keyed.
WRITE_BACK_CONFIRM = Confirm.PLAN
WRITE_BACK_IDEMPOTENCY = Idempotency.KEY_REQUIRED


class WriteBackPreviewRequest(BaseModel):
    """A connector write-back candidate, always evaluated as a preview."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector: str = Field(min_length=1)
    target: str = Field(min_length=1)
    change: dict[str, Any]
    idempotency_key: str = Field(min_length=1)


class WriteBackPreviewResult(BaseModel):
    """What a D18 write-back preview returns: a plan, never an applied write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector: str
    target: str
    idempotency_key: str
    would_apply: dict[str, Any]
    applied: bool = False


class WriteBackRefused(ValueError):
    """Raised when a write-back is attempted outside the preview protocol."""


def preview_write_back(request: WriteBackPreviewRequest) -> WriteBackPreviewResult:
    """Return the typed, idempotent preview for a D18 write-back candidate.

    This is the only entry point this module exposes. There is no apply/execute
    counterpart here: D18 write-back is exposed solely as a previewed registry
    operation (``Confirm.PLAN``, ``Idempotency.KEY_REQUIRED`` at the registry
    layer), never as a native child tool that writes directly.
    """

    if not request.idempotency_key.strip():
        raise WriteBackRefused(
            "write-back preview requires a non-empty idempotency key"
        )
    return WriteBackPreviewResult(
        connector=request.connector,
        target=request.target,
        idempotency_key=request.idempotency_key,
        would_apply=dict(request.change),
        applied=False,
    )


__all__ = [
    "WRITE_BACK_CONFIRM",
    "WRITE_BACK_IDEMPOTENCY",
    "WriteBackPreviewRequest",
    "WriteBackPreviewResult",
    "WriteBackRefused",
    "preview_write_back",
]
