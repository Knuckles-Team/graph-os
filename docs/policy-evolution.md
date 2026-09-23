# Policy evolution

Graph OS owns the service-boundary half of open-weight policy evolution.
epistemic-graph (EG) records every policy fact: the capability, captures,
model-policy versions, training runs and held-out evaluations. The
agent-utilities training path captures episodes and emits digest-bound jobs.
An external trainer does the differentiation. Graph OS decides whether a
training attempt may run and where, holds the resource lease for it, delivers
the job to the trainer, and moves the release pointer.

Training and promotion stay off until the EG capability record enables its
`train` or `promote` control. The verified caller must also hold the
authorization scope that control names.

## Training admission

A host is eligible for a training attempt only when both of these hold:

- It is inside every deployment pressure limit: inode use and free storage
  on `/`, available memory, the vLLM queue age, and the Memory, Disk and PID
  pressure conditions of the k8s node.
- Its EG capacity ledger still has the requested GPU memory free.

The default host-facts source reads these signals from the deployment's
Prometheus through the bounded, allowlisted signal provider the fleet
autoscaler uses. A host with a missing or stale signal is refused.

Each training host has one EG `CapacityCell`. The cell's reserved floor is
the reservation of the protected inference models. Training leases at the
one priority that may never spend a floor, so the engine enforces the
inference SLO reserve. One fenced `CapacityLease` covers one attempt. If the
lease is lost, the job is cancelled.

## Trainer delivery

The trainer is an A2A agent that registers in EG's server registry with
`a2a_role: policy-trainer` and its image digest. Graph OS sends the job as one
`message/send`, follows it with `tasks/get`, and cancels it with
`tasks/cancel`. If no live trainer is registered, the attempt is refused
before any job is sent. Only a completed task with a valid JSON report counts
as a success.

## Release pointer

`graph_policy_release` and its REST twin `POST /graph/policy/release` read,
promote and roll back the pointer for one model family and channel. The
channel is `canary` or `stable`.

| Action | Requires |
|---|---|
| `status` | a verified session |
| `promote` | the capability's `promote` control and scope, plus an accepted, safety-passed, non-regressing held-out evaluation of the version being promoted, whose baseline is the live version |
| `rollback` | the capability's `promote` control and scope; the target must be the version the pointer replaced |

The pointer is an EG node. Every move is an engine compare-and-swap on the
revision and version the operator last read.
