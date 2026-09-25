"""graph-os half of open-weight policy evolution (EH-347).

EG records every policy fact; AU captures and emits jobs; an external
trainer differentiates. This package is what the design leaves to graph-os:
admission of training attempts against host pressure and the protected
inference SLO, the fenced resource lease one attempt holds, the leased
trainer seam AU's ``PolicyTrainingPath`` runs through, and compare-and-swap
promotion/rollback of the model-policy release pointer gated on an accepted
held-out ``PolicyEvaluation``. Train and promote stay off until the EG
capability record enables them.
"""

from .admission import (
    PolicyTrainingAdmission,
    TrainingLeaseBook,
    choose_training_host,
)
from .dispatch import HostFacts, LeasedTrainerDispatcher, TrainerTransport
from .eg_capacity import EgCapacityLeaseBook, training_cell_id
from .eg_pointer import EgReleasePointerRepository
from .models import (
    HostCapacity,
    HostLimits,
    HostPressure,
    InferenceSloPolicy,
    ModelPolicyReleasePointer,
    PolicyEvolutionControlError,
    PolicyReleaseMutation,
    ProtectedModel,
    TrainingAdmissionRequest,
    TrainingLease,
)
from .promotion import ModelPolicyReleaseService, ReleasePointerRepository
from .records import PolicyRecordReader

__all__ = [
    "HostCapacity",
    "HostFacts",
    "HostLimits",
    "HostPressure",
    "EgCapacityLeaseBook",
    "EgReleasePointerRepository",
    "InferenceSloPolicy",
    "LeasedTrainerDispatcher",
    "ModelPolicyReleasePointer",
    "ModelPolicyReleaseService",
    "PolicyEvolutionControlError",
    "PolicyRecordReader",
    "PolicyReleaseMutation",
    "PolicyTrainingAdmission",
    "ProtectedModel",
    "ReleasePointerRepository",
    "TrainerTransport",
    "TrainingAdmissionRequest",
    "TrainingLease",
    "TrainingLeaseBook",
    "choose_training_host",
    "training_cell_id",
]
