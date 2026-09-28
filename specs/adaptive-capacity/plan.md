# Architecture and implementation plan

## Existing wiring and ownership

GraphOS already has fleet dispatch and admission in `graph_os.fleet` and hosted control-plane modules under `graph_os.control_plane`; extend the dispatch admission point and the single hosted operation registry. The durable graph-engine `CapacityCell` and `CapacityLease` are external public contracts. GraphOS owns only runtime throttling state, policy, measurements and receipts. Do not clone the engine capacity model or add a second fleet call path.

Create cohesive `graph_os.fleet.error_budget`, `throttle_controller` and `throttle_limiter` modules only if the current fleet package lacks an equivalent owner. Define typed `OutcomeSample`, `BudgetWindow`, `ThrottleDecision` and `ThrottleStatus` DTOs. The controller is pure over a window and prior state; the limiter is the sole mutable admission point. Bind those through a `throttle_service` adapter to `capacity.throttle.status` and `capacity.throttle.set_mode` operations. The exact engine method name and schema must be pinned to its published contract before an implementation advertises the feature; until then discovery reports unavailable.

## Decision rule

At window close, classify eligible failures, calculate error fraction and compare with configured budget. Decrease on breach (`max(floor, floor(current * beta))`, `0 < beta < 1`) and increase on a healthy window (`min(engine_headroom, current + alpha)`, `alpha > 0`). `alpha`, `beta`, window size and minimum sample count are explicit versioned configuration, not hidden constants. The effective limit is `min(controller_limit, engine_headroom, policy_limit)`. A policy or engine ceiling reduction takes effect immediately; a rise proceeds through AIMD. Never compare observations across tenant, child, class or policy revision boundaries.

Decision receipts include budget/configuration digest, premise revision, prior state, sample count, error class counts, new limit, mode and correlation ID. They omit raw request content and secrets. Mode change and actual enforcement share the hosted operation audit path. Keep a bounded in-memory cache, reconstructing from durable last decision if a public engine receipt contract exists; after restart with no reliable prior state, start at a conservative floor and report the reason.

## Build sequence

1. Pin the published engine capacity/status/lease contract and existing fleet admission call sites.
2. Implement the pure controller with deterministic state transitions and typed error classification.
3. Insert one limiter in the fleet dispatch path; wire observe-only first and prove no behavior change.
4. Add authorized status/mode operations, audit receipts and policy revision invalidation.
5. Enable enforce mode per child behind explicit configuration, run served fault injection and record acceptance evidence.
