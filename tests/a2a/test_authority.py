"""A2A projection over AU's typed control plane, and fail-closed routing."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.api import (
    AgentControlPlaneUnavailable,
    AgentTaskDispatchResult,
    CapabilityResolution,
    PendingInputAnswerReceipt,
    PendingInputAnswerRequest,
    PendingInputRequest,
    WorkItemPage,
    WorkItemSnapshot,
    WorkItemSubmissionResult,
)
from agent_utilities.api.agent_control_contracts import AgentWorkItemNotCancelable

from graph_os.a2a import authority as authority_module
from graph_os.a2a.authority import (
    A2AIdempotencyConflict,
    A2ATaskNotCancelable,
    WorkItemA2AAuthority,
)
from graph_os.a2a.models import A2AMessage, A2ARouteDecision, A2ATextPart
from graph_os.a2a.routing import A2AAssemblyUnavailable, ControlPlaneA2ARouter

_SESSION = SimpleNamespace(tenant="tenant-a", actor=SimpleNamespace(actor_id="actor-a"))
_OTHER = SimpleNamespace(tenant="tenant-a", actor=SimpleNamespace(actor_id="actor-b"))


def _message(text: str = "do work") -> A2AMessage:
    return A2AMessage(role="user", parts=[A2ATextPart(text=text)], message_id="m-1")


class _ControlPlane:
    """An in-memory stand-in for AU's WorkItem-backed control plane."""

    def __init__(self) -> None:
        self.items: dict[str, WorkItemSnapshot] = {}
        self.dispatched: list[str] = []
        self.cancel_refused = False
        self.dispatch_requests: list[Any] = []
        self.outputs: dict[str, Any] = {}
        self.pending: dict[str, PendingInputRequest] = {}

    async def get_run_output(self, request: Any) -> Any:
        return self.outputs.get(request.run_id)

    async def submit_agent_task(self, request: Any) -> AgentTaskDispatchResult:
        self.dispatch_requests.append(request)
        existing = self.items.get(request.work_item_id)
        if existing is None:
            existing = WorkItemSnapshot(
                work_item_id=request.work_item_id,
                kind="orchestrator_task",
                status="ready",
                metadata=dict(request.metadata),
                version=1,
                updated_at_ms=1_000,
            )
            self.items[request.work_item_id] = existing
        self.dispatched.append(request.job_id)
        created = len(self.dispatched) == 1
        return AgentTaskDispatchResult(
            capability=CapabilityResolution(
                kind="agent",
                name=request.agent_name,
                score=1.0,
                source="caller",
                alternatives=(),
            ),
            admission=WorkItemSubmissionResult(
                item=existing, created=created, replayed=not created
            ),
        )

    async def get_work_item(self, request: Any) -> WorkItemSnapshot | None:
        return self.items.get(request.work_item_id)

    async def get_pending_input(self, request: Any) -> PendingInputRequest | None:
        return self.pending.get(request.work_item_id)

    async def submit_pending_input_answer(
        self, request: PendingInputAnswerRequest
    ) -> PendingInputAnswerReceipt:
        self.pending.pop(request.work_item_id)
        return PendingInputAnswerReceipt(
            work_item_id=request.work_item_id,
            call_id=request.call_id,
            accepted=True,
        )

    async def list_work_items(self, request: Any) -> WorkItemPage:
        ordered = sorted(self.items)
        start = int(request.cursor or 0)
        page = ordered[start : start + request.limit]
        more = start + request.limit < len(ordered)
        return WorkItemPage(
            items=tuple(self.items[key] for key in page),
            next_cursor=str(start + request.limit) if more else None,
        )

    async def cancel_work_item(self, request: Any) -> WorkItemSnapshot | None:
        if self.cancel_refused:
            raise AgentWorkItemNotCancelable("in flight")
        item = self.items[request.work_item_id].model_copy(
            update={"status": "cancelled", "version": 2}
        )
        self.items[request.work_item_id] = item
        return item


@pytest.fixture
def bound(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    current = [_SESSION]
    monkeypatch.setattr(authority_module, "_resolve", lambda scope: current[0])
    monkeypatch.setattr(
        authority_module,
        "persistence_reference",
        lambda kind, value, **kwargs: f"{kind}:{kwargs.get('namespace')}:{value}",
    )
    return current


async def test_lifecycle_uses_one_control_plane_store(bound) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    decision = A2ARouteDecision(agent_name="expert", selection_mode="router")

    task = await authority.dispatch(
        message=_message(), idempotency_key="k1", decision=decision
    )
    replay = await authority.dispatch(
        message=_message(), idempotency_key="k1", decision=decision
    )

    assert replay.id == task.id
    assert len(plane.items) == 1
    assert task.status.state == "submitted"
    assert await authority.get(task.id) == task
    tasks, cursor = await authority.list(cursor=None, limit=10)
    assert [listed.id for listed in tasks] == [task.id] and cursor is None
    cancelled = await authority.cancel(task.id)
    assert cancelled.status.state == "canceled"


async def test_pending_call_projects_input_required_and_consumes_exact_answer(
    bound,
) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane, pending_input_enabled=True)
    task = await authority.dispatch(
        message=_message(),
        idempotency_key="pending",
        decision=A2ARouteDecision(agent_name="expert", selection_mode="router"),
    )
    work_item_id = f"workitem:orchestrator:{task.id}"
    plane.items[work_item_id] = plane.items[work_item_id].model_copy(
        update={"status": "running", "version": 2}
    )
    pending = PendingInputRequest(
        work_item_id=work_item_id,
        work_item_version=2,
        call_id="call-1",
        plan_ref="plan-1",
        op="query.uql",
        params_digest="a" * 64,
        origin_principal="actor-a",
        expires_at_ms=int(time.time() * 1000) + 60_000,
        preview="Run the query?",
    )
    plane.pending[work_item_id] = pending
    observed = await authority.get(task.id)
    assert observed is not None
    assert observed.status.state == "input-required"
    assert observed.status.message is not None
    assert observed.status.message.metadata["graphOsApproval"]["call_id"] == "call-1"
    listed, _cursor = await authority.list(cursor=None, limit=10)
    assert listed[0].status.state == "input-required"
    receipt = await authority.answer_pending_input(
        task.id,
        PendingInputAnswerRequest(
            work_item_id=work_item_id,
            work_item_version=2,
            call_id="call-1",
            plan_ref="plan-1",
            op="query.uql",
            params_digest="a" * 64,
            decision="approve",
            idempotency_key="answer-1",
        ),
    )
    assert receipt.accepted is True
    assert (await authority.get(task.id)).status.state == "working"


async def test_pending_exchange_stays_disabled_without_native_port(bound) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    task = await authority.dispatch(
        message=_message(),
        idempotency_key="disabled",
        decision=A2ARouteDecision(agent_name="expert", selection_mode="router"),
    )
    work_item_id = f"workitem:orchestrator:{task.id}"
    plane.items[work_item_id] = plane.items[work_item_id].model_copy(
        update={"status": "running", "version": 2}
    )
    assert (await authority.get(task.id)).status.state == "working"
    with pytest.raises(
        AgentControlPlaneUnavailable, match="A2A task approval is unavailable"
    ):
        await authority.answer_pending_input(
            task.id,
            PendingInputAnswerRequest(
                work_item_id=work_item_id,
                work_item_version=2,
                call_id="call-1",
                plan_ref="plan-1",
                op="query.uql",
                params_digest="a" * 64,
                decision="approve",
                idempotency_key="answer-1",
            ),
        )


async def test_reused_key_with_a_different_request_conflicts(bound) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    decision = A2ARouteDecision(agent_name="expert", selection_mode="router")
    await authority.dispatch(
        message=_message("a"), idempotency_key="k", decision=decision
    )

    with pytest.raises(A2AIdempotencyConflict):
        await authority.dispatch(
            message=_message("b"), idempotency_key="k", decision=decision
        )


async def test_another_owner_cannot_read_list_or_cancel(bound) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    task = await authority.dispatch(
        message=_message(),
        idempotency_key="k",
        decision=A2ARouteDecision(agent_name="expert", selection_mode="router"),
    )
    bound[0] = _OTHER

    assert await authority.get(task.id) is None
    assert (await authority.list(cursor=None, limit=10))[0] == []
    with pytest.raises(A2ATaskNotCancelable):
        await authority.cancel(task.id)


async def test_cursor_is_bound_to_its_owner(bound) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    decision = A2ARouteDecision(agent_name="expert", selection_mode="router")
    for key in ("k1", "k2"):
        await authority.dispatch(
            message=_message(), idempotency_key=key, decision=decision
        )

    first, cursor = await authority.list(cursor=None, limit=1)
    assert len(first) == 1 and cursor is not None
    second, final = await authority.list(cursor=cursor, limit=1)
    assert len(second) == 1 and final is None
    bound[0] = _OTHER
    with pytest.raises(ValueError, match="cursor"):
        await authority.list(cursor=cursor, limit=1)


async def test_in_flight_cancel_is_not_cancelable(bound) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    task = await authority.dispatch(
        message=_message(),
        idempotency_key="k",
        decision=A2ARouteDecision(agent_name="expert", selection_mode="router"),
    )
    plane.cancel_refused = True

    with pytest.raises(A2ATaskNotCancelable):
        await authority.cancel(task.id)


async def test_assembled_tool_subset_and_task_travel_with_the_dispatch(
    bound,
) -> None:
    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)

    await authority.dispatch(
        message=_message(),
        idempotency_key="key",
        decision=A2ARouteDecision(
            agent_name="expert",
            selected_tools=("graph_query",),
            selection_mode="eg-agent-assemble",
            task_iri="eg:task/research",
        ),
    )

    request = plane.dispatch_requests[0]
    assert request.allowed_tools == ("graph_query",)
    assert request.task_iri == "eg:task/research"


async def test_output_reads_only_an_owned_succeeded_run(bound) -> None:
    from agent_utilities.api import RunOutput

    plane = _ControlPlane()
    authority = WorkItemA2AAuthority(lambda session: plane)
    task = await authority.dispatch(
        message=_message(),
        idempotency_key="k",
        decision=A2ARouteDecision(agent_name="expert", selection_mode="router"),
    )
    plane.outputs[task.id] = RunOutput(run_id=task.id, status="succeeded", output="42")

    assert await authority.output(task.id) == "42"
    plane.outputs[task.id] = RunOutput(run_id=task.id, status="running")
    assert await authority.output(task.id) is None
    bound[0] = _OTHER
    assert await authority.output(task.id) is None


@pytest.fixture
def session_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_utilities.api.resolve_session", lambda *a, **k: _SESSION)


async def test_a_budget_never_reaches_capability_search(session_bound) -> None:
    router = ControlPlaneA2ARouter(
        lambda session: pytest.fail("control plane accessed")
    )
    with pytest.raises(A2AAssemblyUnavailable, match="budgeted routing"):
        await router.route(_message(), context_budget_tokens=4096)


async def test_unbudgeted_route_resolves_an_authorized_agent(session_bound) -> None:
    class Plane:
        async def resolve_capability(self, request: Any) -> CapabilityResolution:
            assert request.task_iri == "eg:task/review"
            return CapabilityResolution(
                kind="agent",
                name="expert",
                component_id="agent:expert",
                score=0.9,
                source="eg_search",
                alternatives=(),
            )

    router = ControlPlaneA2ARouter(lambda session: Plane())
    message = A2AMessage(
        role="user",
        parts=[A2ATextPart(text="do work")],
        message_id="m",
        metadata={"graphOsTaskIris": ["eg:task/review"]},
    )

    decision = await router.route(message, context_budget_tokens=None)

    assert decision.agent_name == "expert"
    assert decision.task_iri == "eg:task/review"
    assert decision.selected_tools == ()


async def test_untyped_free_text_is_refused_before_any_search(session_bound) -> None:
    router = ControlPlaneA2ARouter(
        lambda session: pytest.fail("control plane accessed")
    )
    with pytest.raises(A2AAssemblyUnavailable, match="typed task"):
        await router.route(_message(), context_budget_tokens=None)
