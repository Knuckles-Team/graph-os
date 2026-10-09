# Delivery tasks and evidence

- [ ] Identify the current fleet admission point and published engine CapacityCell/lease methods; record exact package versions and contract digest.
- [ ] Add a pure, partitioned AIMD controller and deterministic budget-window fixtures.
  - [ ] GRAPHOS-CAPACITY-R001.1 (child, producer): `graph_os/fleet/error_budget.py` + `tests/fleet/test_error_budget.py`.
- [ ] Connect one limiter to the existing fleet dispatch path; prove observe mode does not enforce.
- [ ] Add `capacity.throttle.status` and `capacity.throttle.set_mode` to the shared hosted operation registry with exact scopes, audit reservation and parity tests.
- [ ] Prove fail-closed stale premise, unavailable policy/audit, tenant isolation and restart behavior.
- [ ] Run all portable source, scanner and package gates on an exact commit; attach CI and artifact identity.
- [ ] Run disposable served fault injection for observe and enforce modes; attach redacted decision and refusal receipts.

**Evidence:** None recorded for this complete GraphOS contract. Existing fleet/control-plane source is wiring inventory, not acceptance proof.
