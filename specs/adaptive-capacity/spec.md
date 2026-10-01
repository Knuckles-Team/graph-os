# Adaptive capacity control

**Owner:** graph-os. **Requirement IDs:** GRAPHOS-CAPACITY-R001; GraphOS interface GRAPHOS-CAPACITY-R002. **Spec state:** ready to build. **Delivery state:** source not verified. **Acceptance state:** not accepted.

## State legend

Ready to build means the contract and tests are specified. Implemented means source exists. Verified means the identified source commit passes deterministic and quality checks. Released means that exact commit is on the public default branch. Accepted means served observation and negative cases pass against the released package. Each claim requires an exact revision and test receipt in `tasks.md`.

## Outcome

GraphOS protects each admitted child/service and tenant from an error cascade by observing a bounded error budget and adjusting request concurrency with additive increase and multiplicative decrease (AIMD). Automatic control may only narrow an engine-declared `CapacityCell` limit; increasing above the engine limit or changing a durable lease requires an authorized capacity operation. A caller can inspect controller status and select observe or enforce mode with `capacity:admin`, but cannot inject measurements or bypass the policy through another surface.

## Requirements

1. Partition state by tenant, child identity, operation class and policy revision. The canonical maximum and current capacity lease come from the installed graph-engine client. A stale, missing or unauthorized capacity premise fails closed for new governed work; existing in-flight work is not silently terminated.
2. The controller consumes bounded windows of real outcome classes: success, retryable service error, timeout, permanent caller error and policy denial. Only eligible service failures consume the child error budget. Caller errors and authorization refusals never teach the controller that the child is unhealthy.
3. On budget breach, multiplicatively reduce the allowed concurrency or rate to a configurable floor, never above the current engine cell/lease. On a healthy completed window, add a bounded increment up to that same ceiling. Persist a deterministic decision receipt with input window digest, old/new limits, mode, reason and policy version. A retry of the same window is idempotent.
4. Observe mode computes and records decisions but does not enforce them. Enforce mode applies the narrower limit at the GraphOS admission boundary before delegated fleet calls. Mode changes require a verified administrator, audit reservation and a policy recheck; `capacity:read` permits status only. Disabled/unavailable policy cannot turn a refusal into an automatic allow.
5. Disconnected children, missing telemetry and expired premises are distinguishable. The controller does not manufacture success from silence. Recovery uses conservative bounded probing with jitter and a circuit state visible in status, not a burst to the old ceiling.
6. MCP and HTTP capacity operations use the same registry, identity, service and error envelope as other [hosted operations](../hosted-api-operations/spec.md). No separate administrator endpoint or state authority is introduced.

## Acceptance

A clean-checkout deterministic fixture demonstrates decrease, gradual recovery, ceiling/floor bounds, tenant isolation and observe/enforce parity. A disposable served test proves one child with injected retryable failures is throttled while another tenant/child remains unaffected. Exact revision, quality-gate results and decision receipts are required before marking this accepted.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
