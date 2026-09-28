# A2A task and approval projection — tasks

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. See [design](plan.md) and [tests](test-spec.md).

- [ ] Inventory published A2A/WorkItem/assembly/grant/plan/audit contracts and existing GraphOS composition; record unavailable dependencies.
- [ ] Extend the existing A2A method table for durable stream/resubscribe and hosted-operation invoke; keep one task authority.
- [ ] Implement parent-child WorkItem and pending-call projection with bounded event cursors and restart-safe `input-required` state.
- [ ] Bind signed original-human grant and atomic plan fence to the pending call; keep confirmation disabled until served proof.
- [ ] Wire all operations and loaded fleet tools through hosted `invoke`, its exact scopes/policy and audit reservation.
- [ ] Execute all positive, negative, replay, cross-tenant, cancellation, policy-change and restart tests in [test-spec.md](test-spec.md).
- [ ] Run clean-checkout language, scanner, package and ephemeral served gates; record exact revision evidence and only then enable approval/mark accepted.
