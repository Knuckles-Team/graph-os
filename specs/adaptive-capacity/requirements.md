# GRAPHOS-CAPACITY-001 requirements

Every requirement this specification owns, with the proof that closes it. Delivery state and
public evidence for each ID are recorded in [`status.json`](status.json); this file defines what
each ID means. The design is in [`spec.md`](spec.md) and [`plan.md`](plan.md), the test contract
in [`test-spec.md`](test-spec.md), and the work order in [`tasks.md`](tasks.md).

| ID | Requirement | Verification |
|---|---|---|
| `GRAPHOS-CAPACITY-R001` | **AIMD error-budget throttling that only narrows automatically.** graph-os observes a bounded error budget per tenant, child, and operation class and applies additive-increase/multiplicative-decrease throttling against the graph engine's declared capacity cell limit. An automatic control action may only narrow the allowed concurrency, never raise it above that ceiling, and every decision is recorded with its input window, old and new limits, and reason. | Proven by a deterministic fixture test demonstrating decrease on budget breach, bounded recovery, and an idempotent decision receipt for a replayed window. |
| `GRAPHOS-CAPACITY-R002` | **Capacity status and mode operations.** graph-os exposes capacity operations in graph_os/api/ops/capacity.py and a read/config facade in graph_os/fleet/throttle_service.py that report controller status, update cell limits, report throttle state, and select observe or enforce mode per child, using the same hosted-operation registry, identity, and error handling as other operations. | Proven by contract tests asserting a mode change requires verified administrator authorization while capacity:read permits status only. |
