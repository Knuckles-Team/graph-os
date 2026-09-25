"""graph-os half of EH-347: training admission, leases, leased trainer, promotion.

EG records are read through a fake ``PolicyEvolutionClient.get`` returning the
generated view shape (``record_id``, ``record.kind``, ``record.record``).
"""

from __future__ import annotations

import asyncio
import importlib.util
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.a2a.policy_training import compose_policy_training_path, policy_records
from graph_os.control_plane.policy_evolution import (
    EgCapacityLeaseBook,
    EgReleasePointerRepository,
    HostCapacity,
    HostLimits,
    HostPressure,
    InferenceSloPolicy,
    LeasedTrainerDispatcher,
    ModelPolicyReleaseService,
    PolicyEvolutionControlError,
    PolicyReleaseMutation,
    PolicyTrainingAdmission,
    ProtectedModel,
    TrainingAdmissionRequest,
    choose_training_host,
    training_cell_id,
)

from .eg_fakes import fake_eg_client

GIB = 1024**3
CAP = "polcap:" + "a" * 64
V1 = "polver:" + "1" * 64
V2 = "polver:" + "2" * 64
EVAL = "poleval:" + "e" * 64
SCOPE = "policy:homelab"


class _Verdict(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def _control(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, scope=SCOPE if enabled else None)


def _capability(train: bool = True, promote: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        controls=SimpleNamespace(
            capture=_control(False), train=_control(train), promote=_control(promote)
        )
    )


def _evaluation(
    version: str = V2,
    baseline: str | None = V1,
    verdict: object = "accepted",
    safety: str = "passed",
    success: int = 900_000,
) -> SimpleNamespace:
    return SimpleNamespace(
        version_id=version,
        baseline_version_id=baseline,
        verdict=verdict,
        safety=safety,
        metrics=SimpleNamespace(
            task_success_ppm=success, baseline_task_success_ppm=800_000
        ),
    )


class _Records:
    def __init__(self) -> None:
        self._bodies: dict[str, tuple[str, Any]] = {}

    def put(self, record_id: str, kind: str, body: Any) -> None:
        self._bodies[record_id] = (kind, body)

    async def get(self, record_id: str) -> Any:
        entry = self._bodies.get(record_id)
        if entry is None:
            return None
        kind, body = entry
        return SimpleNamespace(
            record_id=record_id, record=SimpleNamespace(kind=kind, record=body)
        )


def _records(capability: SimpleNamespace | None = None) -> _Records:
    records = _Records()
    records.put(CAP, "capability", capability or _capability())
    records.put(V1, "model_policy_version", SimpleNamespace())
    records.put(V2, "model_policy_version", SimpleNamespace())
    records.put(EVAL, "policy_evaluation", _evaluation())
    return records


LIMITS = HostLimits(
    max_inode_used_ppm=800_000,
    min_memory_available_bytes=8 * GIB,
    min_storage_free_bytes=50 * GIB,
    max_queue_age_ms=60_000,
)
SLO = InferenceSloPolicy(
    required_models=6,
    protected=tuple(
        ProtectedModel(
            model_id=f"model-{index}",
            host_id="gb10",
            reserved_gpu_memory_bytes=12 * GIB,
        )
        for index in range(6)
    ),
)
HOSTS = (
    HostCapacity(host_id="gb10", gpu_memory_total_bytes=120 * GIB),
    HostCapacity(host_id="r820", gpu_memory_total_bytes=24 * GIB),
)


def _pressure(host: str, **overrides: Any) -> HostPressure:
    values: dict[str, Any] = {
        "host_id": host,
        "inode_used_ppm": 100_000,
        "memory_available_bytes": 64 * GIB,
        "storage_free_bytes": 500 * GIB,
        "queue_age_ms": 0,
        "pod_pressure": False,
    }
    values.update(overrides)
    return HostPressure(**values)


def _healthy() -> dict[str, HostPressure]:
    return {host.host_id: _pressure(host.host_id) for host in HOSTS}


def _request(
    work_item: str = "wi-1", gib: int = 30, **extra: Any
) -> TrainingAdmissionRequest:
    return TrainingAdmissionRequest(
        tenant_id="tenant-a",
        capability_id=CAP,
        work_item_id=work_item,
        requested_gpu_memory_bytes=gib * GIB,
        granted_scopes=frozenset({SCOPE}),
        lease_ttl_ms=600_000,
        **extra,
    )


FREE = {"gb10": 48 * GIB, "r820": 24 * GIB}


# --------------------------------------------------------------- host choice


def test_most_free_eligible_host() -> None:
    assert (
        choose_training_host(_request(gib=20), HOSTS, _healthy(), LIMITS, FREE)
        == "gb10"
    )


def test_a_host_without_free_capacity_is_the_slo_reserve() -> None:
    with pytest.raises(PolicyEvolutionControlError) as refused:
        choose_training_host(_request(gib=49), HOSTS, _healthy(), LIMITS, FREE)
    assert refused.value.code == "TRAINING_SLO_RESERVE"


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"inode_used_ppm": 900_000}, "TRAINING_HOST_PRESSURE_INODE"),
        ({"memory_available_bytes": GIB}, "TRAINING_HOST_PRESSURE_MEMORY"),
        ({"storage_free_bytes": GIB}, "TRAINING_HOST_PRESSURE_STORAGE"),
        ({"queue_age_ms": 120_000}, "TRAINING_HOST_PRESSURE_QUEUE_AGE"),
        ({"pod_pressure": True}, "TRAINING_HOST_PRESSURE_POD_PRESSURE"),
    ],
)
def test_a_pressured_host_is_refused(override: dict[str, Any], code: str) -> None:
    pressure = {"gb10": _pressure("gb10", **override)}
    request = _request(host_constraint=frozenset({"gb10"}))
    with pytest.raises(PolicyEvolutionControlError) as refused:
        choose_training_host(request, HOSTS, pressure, LIMITS, FREE)
    assert refused.value.code == code


def test_an_unobserved_host_is_refused() -> None:
    with pytest.raises(PolicyEvolutionControlError) as refused:
        choose_training_host(_request(), HOSTS, {}, LIMITS, FREE)
    assert refused.value.code == "TRAINING_HOST_PRESSURE_UNOBSERVED"


# ------------------------------------------------------------------ admission


def _admission(
    records: _Records | None = None, slo: InferenceSloPolicy = SLO
) -> PolicyTrainingAdmission:
    book = EgCapacityLeaseBook(
        fake_eg_client(),
        tenant_ref="tenant-a",
        owner_digest="graph-os",
        policy_digest="slo-v1",
    )
    return PolicyTrainingAdmission(records or _records(), book, slo, LIMITS)


async def test_the_ledger_floor_is_the_inference_reserve() -> None:
    admission = _admission()
    first = await admission.admit(_request("wi-1", gib=30), HOSTS, _healthy(), 0)
    assert first.host_id == "gb10" and first.cell_id == training_cell_id("gb10")
    # gb10: 120 GiB capacity, 72 GiB floor, 30 leased -> 18 free; r820 24 free.
    second = await admission.admit(_request("wi-2", gib=20), HOSTS, _healthy(), 0)
    assert second.host_id == "r820"
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await admission.admit(_request("wi-3", gib=30), HOSTS, _healthy(), 0)
    assert refused.value.code == "TRAINING_SLO_RESERVE"
    await admission.leases.release(first, 0)
    third = await admission.admit(_request("wi-3", gib=30), HOSTS, _healthy(), 0)
    assert third.host_id == "gb10" and third.fence_token > first.fence_token


async def test_a_repeated_attempt_replays_its_one_lease() -> None:
    admission = _admission()
    first = await admission.admit(_request("wi-1", gib=10), HOSTS, _healthy(), 0)
    again = await admission.admit(_request("wi-1", gib=10), HOSTS, _healthy(), 0)
    assert again == first


async def test_a_changed_reserve_advances_the_cell_epoch_and_stales_leases() -> None:
    admission = _admission()
    lease = await admission.admit(_request("wi-1", gib=10), HOSTS, _healthy(), 0)
    assert await admission.leases.is_live(lease, 1)
    tighter = InferenceSloPolicy(
        required_models=6,
        protected=(
            *SLO.protected[:5],
            SLO.protected[5].model_copy(update={"reserved_gpu_memory_bytes": 20 * GIB}),
        ),
    )
    await admission.leases.provision(HOSTS[0], tighter.reserved_on("gb10"), 2)
    assert not await admission.leases.is_live(lease, 3)


async def test_an_incomplete_slo_policy_fails_closed() -> None:
    partial = InferenceSloPolicy(required_models=6, protected=SLO.protected[:5])
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await _admission(slo=partial).admit(_request(), HOSTS, _healthy(), 0)
    assert refused.value.code == "TRAINING_SLO_POLICY_INCOMPLETE"


@pytest.mark.parametrize(
    ("records", "scopes", "code"),
    [
        (_Records(), frozenset({SCOPE}), "POLICY_CAPABILITY_MISSING"),
        (None, frozenset({"other"}), "POLICY_SCOPE_NOT_GRANTED"),
    ],
)
async def test_admission_requires_the_capability_and_its_scope(
    records: _Records | None, scopes: frozenset[str], code: str
) -> None:
    request = _request().model_copy(update={"granted_scopes": scopes})
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await _admission(records).admit(request, HOSTS, _healthy(), 0)
    assert refused.value.code == code


async def test_train_is_off_unless_the_capability_enables_it() -> None:
    admission = _admission(_records(_capability(train=False)))
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await admission.admit(_request(), HOSTS, _healthy(), 0)
    assert refused.value.code == "POLICY_TRAIN_DISABLED"


# ------------------------------------------------------------ leased trainer


class _Hosts:
    async def snapshot(self) -> tuple[Any, dict[str, HostPressure]]:
        return HOSTS, _healthy()


class _Transport:
    def __init__(self, hold: asyncio.Event | None = None) -> None:
        self.hold = hold
        self.cancelled = False
        self.leases: list[Any] = []

    async def run(self, spec: Any, lease: Any) -> Any:
        self.leases.append(lease)
        if self.hold is not None:
            await self.hold.wait()
        return SimpleNamespace(status="succeeded")

    async def cancel(self, spec: Any, lease: Any) -> Any:
        self.cancelled = True
        return SimpleNamespace(status="cancelled")


def _spec(capability: str = CAP) -> Any:
    return SimpleNamespace(policy=SimpleNamespace(capability_id=capability))


def _dispatcher(
    admission: PolicyTrainingAdmission, transport: _Transport
) -> LeasedTrainerDispatcher:
    return LeasedTrainerDispatcher(
        admission,
        _request(),
        transport,
        _Hosts(),
        lambda: 0,
        lease_check_interval_s=0.01,
    )


async def test_the_job_runs_under_a_lease_that_is_released_afterwards() -> None:
    admission = _admission()
    transport = _Transport()
    outcome = await _dispatcher(admission, transport).run(_spec())
    assert outcome.status == "succeeded"
    assert transport.leases
    assert not await admission.leases.is_live(transport.leases[0], 0)
    assert await admission.leases.free_bytes("gb10", 0) == 48 * GIB


async def test_lease_loss_cancels_the_external_job() -> None:
    client = fake_eg_client()
    book = EgCapacityLeaseBook(
        client, tenant_ref="tenant-a", owner_digest="graph-os", policy_digest="slo-v1"
    )
    admission = PolicyTrainingAdmission(_records(), book, SLO, LIMITS)
    transport = _Transport(hold=asyncio.Event())
    running = asyncio.ensure_future(_dispatcher(admission, transport).run(_spec()))
    while not transport.leases:
        await asyncio.sleep(0)
    client.capacity_leases.revoke(transport.leases[0].lease_id)
    outcome = await asyncio.wait_for(running, timeout=5)
    assert outcome.status == "cancelled" and transport.cancelled


async def test_a_job_for_another_capability_is_refused() -> None:
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await _dispatcher(_admission(), _Transport()).run(_spec("polcap:" + "b" * 64))
    assert refused.value.code == "TRAINING_SPEC_MISMATCH"


# ------------------------------------------------------------------ promotion


def _mutation(**overrides: Any) -> PolicyReleaseMutation:
    values: dict[str, Any] = {
        "tenant_id": "tenant-a",
        "family": "qwen-policy",
        "channel": "stable",
        "operation": "promote",
        "capability_id": CAP,
        "expected_revision": 0,
        "expected_version_id": None,
        "next_version_id": V1,
        "evaluation_id": EVAL,
        "granted_scopes": frozenset({SCOPE}),
        "change_ref": "change-1",
    }
    values.update(overrides)
    return PolicyReleaseMutation(**values)


def _service(records: _Records) -> ModelPolicyReleaseService:
    return ModelPolicyReleaseService(
        records, EgReleasePointerRepository(fake_eg_client())
    )


async def _live_v1(records: _Records) -> ModelPolicyReleaseService:
    records.put(EVAL + "0", "policy_evaluation", _evaluation(version=V1, baseline=None))
    service = _service(records)
    await service.apply(_mutation(evaluation_id=EVAL + "0"))
    return service


async def test_promotion_moves_the_pointer_by_cas_and_rollback_returns() -> None:
    records = _records()
    service = await _live_v1(records)
    promoted = await service.apply(
        _mutation(expected_revision=1, expected_version_id=V1, next_version_id=V2)
    )
    assert (promoted.version_id, promoted.revision, promoted.previous_version_id) == (
        V2,
        2,
        V1,
    )
    rolled = await service.apply(
        _mutation(
            operation="rollback",
            expected_revision=2,
            expected_version_id=V2,
            next_version_id=V1,
            evaluation_id=None,
        )
    )
    assert (rolled.version_id, rolled.revision, rolled.evaluation_id) == (V1, 3, None)


@pytest.mark.parametrize(
    ("evaluation", "code"),
    [
        (_evaluation(verdict=_Verdict.REJECTED), "RELEASE_EVALUATION_NOT_ACCEPTED"),
        (_evaluation(safety="failed"), "RELEASE_EVALUATION_NOT_ACCEPTED"),
        (_evaluation(success=700_000), "RELEASE_EVALUATION_NOT_ACCEPTED"),
        (_evaluation(version=V1), "RELEASE_EVALUATION_MISMATCH"),
        (
            _evaluation(baseline="polver:" + "9" * 64),
            "RELEASE_EVALUATION_BASELINE_STALE",
        ),
    ],
)
async def test_promotion_needs_an_accepted_held_out_evaluation(
    evaluation: SimpleNamespace, code: str
) -> None:
    records = _records()
    service = await _live_v1(records)
    records.put(EVAL, "policy_evaluation", evaluation)
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await service.apply(
            _mutation(expected_revision=1, expected_version_id=V1, next_version_id=V2)
        )
    assert refused.value.code == code
    live = await service.current("tenant-a", "qwen-policy", "stable")
    assert live is not None and live.version_id == V1


async def test_promote_is_off_unless_the_capability_enables_it() -> None:
    service = _service(_records(_capability(promote=False)))
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await service.apply(_mutation())
    assert refused.value.code == "POLICY_PROMOTE_DISABLED"


async def test_a_stale_expected_revision_is_a_cas_conflict() -> None:
    service = await _live_v1(_records())
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await service.apply(_mutation(next_version_id=V2))
    assert refused.value.code == "RELEASE_CAS_CONFLICT"


async def test_rollback_only_returns_to_the_replaced_version() -> None:
    service = await _live_v1(_records())
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await service.apply(
            _mutation(
                operation="rollback",
                expected_revision=1,
                expected_version_id=V1,
                next_version_id=V2,
                evaluation_id=None,
            )
        )
    assert refused.value.code == "RELEASE_ROLLBACK_TARGET_INVALID"


def test_a_promotion_without_an_evaluation_is_malformed() -> None:
    with pytest.raises(ValueError, match="held-out evaluation"):
        _mutation(evaluation_id=None)


# --------------------------------------------------------------- composition


def test_a_client_without_policy_evolution_fails_closed() -> None:
    with pytest.raises(PolicyEvolutionControlError) as refused:
        policy_records(SimpleNamespace())
    assert refused.value.code == "POLICY_EVOLUTION_UNAVAILABLE"


def test_composition_fails_closed_until_au_publishes_the_training_path() -> None:
    published = importlib.util.find_spec("agent_utilities.harness.policy_evolution")

    def compose() -> Any:
        return compose_policy_training_path(
            SimpleNamespace(policy_evolution=_records()),
            _admission(),
            _request(),
            _Transport(),
            _Hosts(),
            lambda: 0,
        )

    if published is None:
        with pytest.raises(PolicyEvolutionControlError) as refused:
            compose()
        assert refused.value.code == "POLICY_TRAINING_PATH_UNAVAILABLE"
    else:
        assert type(compose()).__name__ == "PolicyTrainingPath"


# ----------------------------------------------------------- durable pointer


async def test_the_pointer_is_an_eg_node_moved_only_by_engine_cas() -> None:
    client = fake_eg_client()
    repository = EgReleasePointerRepository(client)
    records = _records()
    records.put(EVAL + "0", "policy_evaluation", _evaluation(version=V1, baseline=None))
    service = ModelPolicyReleaseService(records, repository)
    pointer = await service.apply(_mutation(evaluation_id=EVAL + "0"))
    stored = client.nodes.nodes[pointer.pointer_id]
    assert stored["kind"] == "model_policy_release_pointer"
    assert stored["version_id"] == V1 and stored["revision"] == 1
    # A second writer racing the first move loses on the engine's create-if-absent.
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await repository.compare_and_swap(0, None, pointer)
    assert refused.value.code == "RELEASE_CAS_CONFLICT"


async def test_a_tampered_pointer_node_is_refused() -> None:
    client = fake_eg_client()
    repository = EgReleasePointerRepository(client)
    records = _records()
    records.put(EVAL + "0", "policy_evaluation", _evaluation(version=V1, baseline=None))
    pointer = await ModelPolicyReleaseService(records, repository).apply(
        _mutation(evaluation_id=EVAL + "0")
    )
    client.nodes.nodes[pointer.pointer_id]["version_id"] = V2
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await repository.get(pointer.pointer_id)
    assert refused.value.code == "RELEASE_POINTER_TAMPERED"
