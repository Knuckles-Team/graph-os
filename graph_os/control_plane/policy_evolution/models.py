"""Strict graph-os models for the policy-evolution control half (EH-347).

EG owns every policy record (capability, capture, model-policy version,
training run, evaluation). graph-os owns only what the design places at the
service boundary: host/resource facts for admitting a training attempt, the
fenced resource lease that attempt holds, and the release pointer that names
the served model-policy version. Nothing here restates an EG record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from graph_os.control_plane._model import ControlPlaneModel

__all__ = [
    "HostCapacity",
    "HostLimits",
    "HostPressure",
    "InferenceSloPolicy",
    "ModelPolicyReleasePointer",
    "PolicyEvolutionControlError",
    "PolicyReleaseMutation",
    "ProtectedModel",
    "TrainingAdmissionRequest",
    "TrainingLease",
    "release_pointer_digest",
    "release_pointer_id",
]

_Id = Field(min_length=1, max_length=256)


class PolicyEvolutionControlError(ValueError):
    """A typed, fail-closed refusal of a graph-os policy-evolution step."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _digest(*parts: object) -> str:
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HostPressure(ControlPlaneModel):
    """One host's observed pressure; admission refuses a pressured host."""

    host_id: str = _Id
    inode_used_ppm: int = Field(ge=0, le=1_000_000)
    memory_available_bytes: int = Field(ge=0)
    storage_free_bytes: int = Field(ge=0)
    queue_age_ms: int = Field(ge=0)
    pod_pressure: bool


class HostLimits(ControlPlaneModel):
    """Deployment limits a host must be inside before it takes training."""

    max_inode_used_ppm: int = Field(ge=0, le=1_000_000)
    min_memory_available_bytes: int = Field(ge=0)
    min_storage_free_bytes: int = Field(ge=0)
    max_queue_age_ms: int = Field(ge=0)


class HostCapacity(ControlPlaneModel):
    """The GPU memory one host offers the shared inference/training pool."""

    host_id: str = _Id
    gpu_memory_total_bytes: int = Field(ge=0)


class ProtectedModel(ControlPlaneModel):
    """One served model whose inference SLO training must never degrade."""

    model_id: str = _Id
    host_id: str = _Id
    reserved_gpu_memory_bytes: int = Field(ge=0)


class InferenceSloPolicy(ControlPlaneModel):
    """The protected inference set. Training never gets an implicit reserve.

    ``required_models`` is the configured SLO size (six on the homelab); a
    policy that does not name that many distinct models fails closed, so a
    missing reservation can never read as free capacity.
    """

    required_models: int = Field(ge=1)
    protected: tuple[ProtectedModel, ...]

    @model_validator(mode="after")
    def models_are_distinct(self) -> InferenceSloPolicy:
        ids = [model.model_id for model in self.protected]
        if len(ids) != len(set(ids)):
            raise ValueError("a protected model is listed twice")
        return self

    def complete(self) -> bool:
        return len(self.protected) >= self.required_models

    def reserved_on(self, host_id: str) -> int:
        return sum(
            model.reserved_gpu_memory_bytes
            for model in self.protected
            if model.host_id == host_id
        )


class TrainingAdmissionRequest(ControlPlaneModel):
    """One training attempt a verified caller asks graph-os to admit."""

    tenant_id: str = _Id
    capability_id: str = _Id
    work_item_id: str = _Id
    requested_gpu_memory_bytes: int = Field(ge=1)
    granted_scopes: frozenset[str]
    host_constraint: frozenset[str] = frozenset()
    lease_ttl_ms: int = Field(ge=1_000, le=86_400_000)


class TrainingLease(ControlPlaneModel):
    """The fenced EG ``CapacityLease`` one training attempt holds on one host.

    ``lease_epoch``/``fence_token`` are the engine-minted fence: renew and
    release must present them, and a cell repartition (a new epoch) makes the
    lease stale rather than silently valid.
    """

    lease_id: str = _Id
    tenant_id: str = _Id
    host_id: str = _Id
    cell_id: str = _Id
    work_item_id: str = _Id
    capability_id: str = _Id
    gpu_memory_bytes: int = Field(ge=1)
    lease_epoch: int = Field(ge=1)
    fence_token: int = Field(ge=1)
    expires_at_ms: int = Field(ge=0)


def release_pointer_id(tenant_id: str, family: str, channel: str) -> str:
    """The one stable pointer id per tenant / model family / channel."""
    return "policy-release:" + _digest(tenant_id, family, channel)[:32]


def release_pointer_digest(
    pointer_id: str,
    version_id: str,
    revision: int,
    previous_version_id: str | None,
    evaluation_id: str | None,
) -> str:
    return _digest(pointer_id, version_id, revision, previous_version_id, evaluation_id)


class ModelPolicyReleasePointer(ControlPlaneModel):
    """CAS-managed pointer naming the served EG ``ModelPolicyVersion``.

    Canary and rollback both point at immutable version ids; the pointer holds
    identity only, never artifact bytes.
    """

    pointer_id: str = _Id
    tenant_id: str = _Id
    family: str = _Id
    channel: Literal["canary", "stable"]
    version_id: str = _Id
    revision: int = Field(ge=1)
    previous_version_id: str | None = None
    evaluation_id: str | None = None
    pointer_digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def pointer_is_content_addressed(self) -> ModelPolicyReleasePointer:
        if self.pointer_id != release_pointer_id(
            self.tenant_id, self.family, self.channel
        ):
            raise ValueError("release pointer id is not stable")
        if self.previous_version_id == self.version_id:
            raise ValueError("release pointer does not move")
        expected = release_pointer_digest(
            self.pointer_id,
            self.version_id,
            self.revision,
            self.previous_version_id,
            self.evaluation_id,
        )
        if self.pointer_digest != expected:
            raise ValueError("release pointer digest does not match its state")
        return self


class PolicyReleaseMutation(ControlPlaneModel):
    """An explicit promote/rollback request; never an implicit move."""

    tenant_id: str = _Id
    family: str = _Id
    channel: Literal["canary", "stable"]
    operation: Literal["promote", "rollback"]
    capability_id: str = _Id
    expected_revision: int = Field(ge=0)
    expected_version_id: str | None = None
    next_version_id: str = _Id
    evaluation_id: str | None = None
    granted_scopes: frozenset[str]
    change_ref: str = _Id

    @model_validator(mode="after")
    def promotion_names_its_evaluation(self) -> PolicyReleaseMutation:
        if self.operation == "promote" and self.evaluation_id is None:
            raise ValueError("a promotion names its held-out evaluation")
        if (self.expected_revision == 0) != (self.expected_version_id is None):
            raise ValueError("expected revision and version disagree")
        return self
