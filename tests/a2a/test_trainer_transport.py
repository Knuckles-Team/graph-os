"""Outbound A2A trainer transport and its EG server-registry resolution."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.a2a.trainer import A2ATrainerTransport, EgTrainerRegistry
from graph_os.control_plane.policy_evolution import (
    PolicyEvolutionControlError,
    TrainingLease,
)

DIGEST = "d" * 64
LEASE = TrainingLease(
    lease_id="policy-train:abc",
    tenant_id="tenant-a",
    host_id="gb10",
    cell_id="policy-train/gpu/gb10",
    work_item_id="wi-1",
    capability_id="polcap:" + "a" * 64,
    gpu_memory_bytes=1024**3,
    lease_epoch=1,
    fence_token=7,
    expires_at_ms=10_000,
)


@dataclass(frozen=True)
class _Policy:
    capability_id: str = "polcap:" + "a" * 64
    capture_ids: tuple[str, ...] = ("polcapture:1",)


SPEC = SimpleNamespace(job_id="job-1", method="klpo", policy=_Policy())


def _entry(role: str = "policy-trainer", expires: int = 10_000, **extra: Any) -> Any:
    resources = {"a2a_role": role, "trainer_image_digest": DIGEST, **extra}
    return SimpleNamespace(
        name="trainer",
        url="https://trainer.test/a2a",
        desired=SimpleNamespace(value="enabled"),
        lease_expires_at_ms=expires,
        resources=json.dumps(resources),
    )


def _registry(*entries: Any) -> EgTrainerRegistry:
    async def list_all() -> tuple[Any, ...]:
        return entries

    client = SimpleNamespace(server_registry=SimpleNamespace(list_all=list_all))
    return EgTrainerRegistry(client, lambda: 1_000)


@dataclass
class _Trainer:
    """A scripted remote A2A trainer."""

    states: list[str]
    report: str = json.dumps(
        {
            "status": "succeeded",
            "trainer_image_digest": DIGEST,
            "artifact_digest": "e" * 64,
            "artifact_ref": "artifact://lora/1",
            "resources": {"wall_ms": 5},
        }
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(body)
        if body["method"] == "tasks/cancel":
            return {"result": {"id": "t-1", "status": {"state": "canceled"}}}
        state = self.states.pop(0) if self.states else "working"
        task: dict[str, Any] = {"id": "t-1", "status": {"state": state}}
        if state == "completed":
            task["artifacts"] = [{"parts": [{"kind": "text", "text": self.report}]}]
        return {"result": task}


def _outcome(**fields: Any) -> Any:
    return SimpleNamespace(**fields)


def _transport(trainer: _Trainer, *entries: Any) -> A2ATrainerTransport:
    return A2ATrainerTransport(
        _registry(*(entries or (_entry(),))),
        poster=trainer.post,
        outcome=_outcome,
        poll_interval_s=0,
    )


async def test_a_job_is_sent_followed_and_reported() -> None:
    trainer = _Trainer(states=["submitted", "working", "completed"])
    outcome = await _transport(trainer).run(SPEC, LEASE)
    assert (outcome.status, outcome.artifact_ref) == ("succeeded", "artifact://lora/1")
    send = trainer.calls[0]
    assert send["method"] == "message/send"
    message = send["params"]["message"]
    assert message["messageId"] == "job-1"
    job = json.loads(message["parts"][0]["text"])
    assert job["policy"]["capability_id"] == SPEC.policy.capability_id
    assert job["lease"]["lease_id"] == LEASE.lease_id
    assert [call["method"] for call in trainer.calls[1:]] == ["tasks/get", "tasks/get"]


@pytest.mark.parametrize(
    ("state", "report", "status"),
    [
        ("failed", "", "failed"),
        ("rejected", "", "failed"),
        ("canceled", "", "cancelled"),
        ("completed", "not json", "failed"),
        ("completed", json.dumps({"status": "great"}), "failed"),
    ],
)
async def test_nothing_but_a_valid_report_is_a_success(
    state: str, report: str, status: str
) -> None:
    trainer = _Trainer(states=[state], report=report)
    outcome = await _transport(trainer).run(SPEC, LEASE)
    assert outcome.status == status and outcome.trainer_image_digest == DIGEST


async def test_cancel_reaches_the_remote_task() -> None:
    trainer = _Trainer(states=["working"])
    transport = _transport(trainer)
    running = asyncio.ensure_future(transport.run(SPEC, LEASE))
    while len(trainer.calls) < 2:
        await asyncio.sleep(0)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    outcome = await transport.cancel(SPEC, LEASE)
    assert outcome.status == "cancelled"
    assert trainer.calls[-1]["method"] == "tasks/cancel"


@pytest.mark.parametrize(
    "entries",
    [
        (),
        (_entry(role="summarizer"),),
        (_entry(expires=10),),
        (_entry(trainer_image_digest=""),),
    ],
)
async def test_no_live_registered_trainer_is_a_typed_refusal(
    entries: tuple[Any, ...],
) -> None:
    trainer = _Trainer(states=["completed"])
    transport = A2ATrainerTransport(
        _registry(*entries), poster=trainer.post, outcome=_outcome, poll_interval_s=0
    )
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await transport.run(SPEC, LEASE)
    assert refused.value.code == "TRAINING_TRAINER_NOT_REGISTERED"
    assert trainer.calls == []
