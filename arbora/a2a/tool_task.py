"""Reference-bound single-tool tasks run inside one WorkItem lease (ARBORA-A2A-R002.2.2).

A tool task is one admitted operation call. The queue carrier holds only a
content-addressed reference to the immutable request. Arbora resolves the
request through its dispatcher, checks that it matches the carrier, and runs it
inside the claimed WorkItem lease. EG owns the WorkItem record, its lease and
the stored receipt; :class:`WorkItemFence` binds those EG coordination methods.

Once execution starts, an effect is uncertain until a bound result proves its
outcome. An uncertain effect leaves the WorkItem open and the delivery
unacknowledged, so that only the owner can reconcile it. A repeat of the effect
never happens automatically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "TERMINAL_WORK_ITEM_STATES",
    "ToolTaskCarrier",
    "ToolTaskDispatcher",
    "ToolTaskOutcome",
    "ToolTaskRefused",
    "ToolTaskRequest",
    "ToolTaskResult",
    "ToolTaskRun",
    "WorkItemFence",
    "WorkItemLeaseLost",
    "run_tool_task",
    "tool_task_work_item_id",
]

TERMINAL_WORK_ITEM_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "dead_letter"}
)
_MAX_PARAMETER_BYTES = 65_536
_REF_PATTERN = r"^tool-task:sha256:[0-9a-f]{64}$"


class ToolTaskRefused(RuntimeError):
    """A refusal before execution starts; it proves that no effect happened."""


class WorkItemLeaseLost(RuntimeError):
    """The WorkItem claim is no longer current; the worker must stop."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ToolTaskRequest(_Closed):
    """The immutable admitted request. Its digest is the carrier reference.

    Parameters are canonical JSON object data, never a prompt. The authority
    reference resolves to the original caller when the call runs; the worker
    identity grants no permission.
    """

    version: Literal[1] = 1
    operation_id: str = Field(min_length=1, max_length=256)
    parameters_json: str = Field(min_length=2, max_length=_MAX_PARAMETER_BYTES)
    principal: str = Field(min_length=1, max_length=256)
    tenant: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    authority_ref: str = Field(min_length=1, max_length=512)
    policy_revision: str = Field(min_length=1, max_length=256)
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _canonical_parameters(self) -> Self:
        value = json.loads(self.parameters_json)
        if not isinstance(value, dict):
            raise ValueError("tool parameters must be a JSON object")
        if self.parameters_json != _canonical_json(value):
            raise ValueError("tool parameters must be canonical JSON")
        if len(self.parameters_json.encode("utf-8")) > _MAX_PARAMETER_BYTES:
            raise ValueError("tool parameters exceed the byte limit")
        return self

    @property
    def request_ref(self) -> str:
        """The content-addressed reference of the complete request."""
        digest = hashlib.sha256(_canonical_json(self.model_dump()).encode("utf-8"))
        return "tool-task:sha256:" + digest.hexdigest()


class ToolTaskResult(_Closed):
    """The owner's bound outcome of one execution."""

    job_id: str = Field(min_length=1, max_length=256)
    request_ref: str = Field(pattern=_REF_PATTERN)
    principal: str = Field(min_length=1, max_length=256)
    outcome: Literal["completed", "failed", "refused", "indeterminate"]
    receipt_ref: str = Field(min_length=1, max_length=512)


class ToolTaskCarrier(_Closed):
    """The verified queue carrier. It holds a reference, never the inputs."""

    job_id: str = Field(min_length=1, max_length=256)
    payload_ref: str = Field(pattern=_REF_PATTERN)
    tenant: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    allowed_tool_subset: tuple[str, ...] = Field(min_length=1, max_length=1)
    deadline_unix: float | None = None


def tool_task_work_item_id(job_id: str) -> str:
    """The WorkItem that admits one tool-task job."""
    return f"workitem:dispatch:{job_id}"


@runtime_checkable
class ToolTaskDispatcher(Protocol):
    """Arbora resolution and execution of one admitted operation call.

    ``execute`` reauthorizes the original caller through ``authority_ref``
    before any effect. ``job_id`` is the durable idempotency key, and the
    dispatcher stores the full result before it returns. It calls
    ``require_current`` immediately before effects.
    """

    def resolve(
        self, request_ref: str, *, tenant: str, session_id: str
    ) -> ToolTaskRequest: ...

    def execute(
        self,
        request: ToolTaskRequest,
        *,
        job_id: str,
        require_current: Callable[[], None],
    ) -> ToolTaskResult: ...


@runtime_checkable
class WorkItemFence(Protocol):
    """The EG WorkItem coordination methods one tool task uses.

    ``get`` reads the record, ``claim`` returns a claim token or ``None``,
    ``require_current`` renews the lease or raises :class:`WorkItemLeaseLost`,
    and ``commit`` writes the terminal result under the claim.
    """

    def get(self, item_id: str) -> Mapping[str, Any] | None: ...

    def claim(self, item_id: str) -> str | None: ...

    def require_current(self, item_id: str, claim: str) -> None: ...

    def commit(
        self, item_id: str, claim: str, *, succeeded: bool, ref: str
    ) -> None: ...


ToolTaskOutcome = Literal[
    "completed", "failed", "refused", "indeterminate", "fenced", "skipped", "rejected"
]
_ACKNOWLEDGED: frozenset[str] = frozenset({"completed", "failed", "refused", "skipped"})


@dataclass(frozen=True, slots=True)
class ToolTaskRun:
    """The worker outcome and whether the broker delivery may be acknowledged."""

    outcome: ToolTaskOutcome
    detail: str = ""

    @property
    def acknowledge(self) -> bool:
        return self.outcome in _ACKNOWLEDGED


def _is_terminal(record: Mapping[str, Any] | None) -> bool:
    return record is not None and record.get("status") in TERMINAL_WORK_ITEM_STATES


def _resolve(
    carrier: ToolTaskCarrier, dispatcher: ToolTaskDispatcher | None
) -> ToolTaskRequest:
    """Resolve the exact admitted request, or refuse before any effect."""
    if dispatcher is None:
        raise ToolTaskRefused("tool_task_dispatcher_unavailable")
    request = dispatcher.resolve(
        carrier.payload_ref, tenant=carrier.tenant, session_id=carrier.session_id
    )
    if not isinstance(request, ToolTaskRequest):
        raise ToolTaskRefused("tool_task_request_unavailable")
    if (
        request.request_ref != carrier.payload_ref
        or request.tenant != carrier.tenant
        or request.session_id != carrier.session_id
        or carrier.allowed_tool_subset != (request.operation_id,)
    ):
        raise ToolTaskRefused("tool_task_request_mismatch")
    return request


def _bound(
    result: object, carrier: ToolTaskCarrier, request: ToolTaskRequest
) -> ToolTaskResult | None:
    if not isinstance(result, ToolTaskResult):
        return None
    if (
        result.job_id != carrier.job_id
        or result.request_ref != carrier.payload_ref
        or result.principal != request.principal
    ):
        return None
    return result


def _execute(
    carrier: ToolTaskCarrier,
    dispatcher: ToolTaskDispatcher | None,
    require_current: Callable[[], None],
) -> ToolTaskResult | ToolTaskRefused | None:
    """Run the call; ``None`` means the effect is uncertain."""
    try:
        require_current()
        request = _resolve(carrier, dispatcher)
        require_current()
    except ToolTaskRefused as refusal:
        return refusal
    assert dispatcher is not None  # _resolve refuses an absent dispatcher
    try:
        raw = dispatcher.execute(
            request, job_id=carrier.job_id, require_current=require_current
        )
    except WorkItemLeaseLost:
        raise
    except Exception:  # noqa: BLE001 - an exception after execution starts is uncertain
        return None
    return _bound(raw, carrier, request)


def _precheck(
    carrier: ToolTaskCarrier, admitted: Mapping[str, Any] | None, now: float
) -> ToolTaskRun | None:
    """Return the outcome when the delivery must not claim, else ``None``."""
    if admitted is None or (
        admitted.get("payload_ref") != carrier.payload_ref
        or admitted.get("tenant") != carrier.tenant
    ):
        return ToolTaskRun("rejected", "tool_task_work_item_mismatch")
    if _is_terminal(admitted):
        return ToolTaskRun("skipped")
    if carrier.deadline_unix is not None and now > carrier.deadline_unix:
        # A late start admits no effect, and it cannot prove that an earlier
        # attempt had no effect. The owner reconciles the open item.
        return ToolTaskRun("indeterminate", "tool_task_deadline_expired")
    return None


def _settle(
    carrier: ToolTaskCarrier,
    fence: WorkItemFence,
    item_id: str,
    claim: str,
    dispatcher: ToolTaskDispatcher | None,
) -> ToolTaskRun:
    """Execute under the claim and commit only a proven outcome."""

    def require_current() -> None:
        fence.require_current(item_id, claim)

    result = _execute(carrier, dispatcher, require_current)
    if result is None:
        return ToolTaskRun("indeterminate", "tool_task_execution_uncertain")
    if isinstance(result, ToolTaskRefused):
        ref = f"tool-task:{carrier.job_id}:refused:{result}"
        fence.commit(item_id, claim, succeeded=False, ref=ref)
        return ToolTaskRun("refused", str(result))
    if result.outcome == "indeterminate":
        return ToolTaskRun("indeterminate", result.receipt_ref)
    require_current()
    succeeded = result.outcome == "completed"
    fence.commit(item_id, claim, succeeded=succeeded, ref=result.receipt_ref)
    return ToolTaskRun(result.outcome, result.receipt_ref)


def run_tool_task(
    carrier: ToolTaskCarrier,
    *,
    fence: WorkItemFence,
    dispatcher: ToolTaskDispatcher | None,
    now: float,
) -> ToolTaskRun:
    """Claim, run and commit one tool task under its WorkItem lease."""
    item_id = tool_task_work_item_id(carrier.job_id)
    early = _precheck(carrier, fence.get(item_id), now)
    if early is not None:
        return early
    claim = fence.claim(item_id)
    if claim is None:
        return ToolTaskRun("skipped" if _is_terminal(fence.get(item_id)) else "fenced")
    try:
        return _settle(carrier, fence, item_id, claim, dispatcher)
    except WorkItemLeaseLost:
        return ToolTaskRun("fenced")
