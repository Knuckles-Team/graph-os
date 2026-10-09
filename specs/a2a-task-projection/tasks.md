# A2A task and approval projection — tasks

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. See [design](plan.md) and [tests](test-spec.md).

- [ ] Inventory published A2A/WorkItem/assembly/grant/plan/audit contracts and existing GraphOS composition; record unavailable dependencies.
- [ ] Extend the existing A2A method table for durable stream/resubscribe and hosted-operation invoke; keep one task authority.
- [ ] Implement parent-child WorkItem and pending-call projection with bounded event cursors and restart-safe `input-required` state.
- [ ] Bind signed original-human grant and atomic plan fence to the pending call; keep confirmation disabled until served proof.
- [ ] Wire all operations and loaded fleet tools through hosted `invoke`, its exact scopes/policy and audit reservation.
- [ ] Execute all positive, negative, replay, cross-tenant, cancellation, policy-change and restart tests in [test-spec.md](test-spec.md).
- [ ] Run clean-checkout language, scanner, package and ephemeral served gates; record exact revision evidence and only then enable approval/mark accepted.
- [x] Register `tasks/resubscribe` on the A2A method table as a typed,
  validated, explicitly fail-closed stub (GRAPHOS-A2A-R001.1, split from
  GRAPHOS-A2A-R001): `A2AStreamingUnavailable` is raised and returned as a
  distinct JSON-RPC error (`-32010`, HTTP 501) rather than "method not
  found", proven by
  `tests/a2a/test_application.py::test_json_rpc_refuses_resubscribe_until_streaming_is_implemented`.
  The bounded, restart-safe event-cursor backing for real durable streaming
  remains open under the parent requirement.
- [x] Prove no duplicate skill/prompt harvest path (GRAPHOS-A2A-R003): a
  static source-level absence check confirms graph-os never imports
  agent-utilities' legacy `agent_utilities.mcp.multiplexer` /
  `shared_multiplexer`, and that the skill/prompt body-harvest functions
  are defined in exactly one module, `graph_os/fleet/multiplexer.py`.
- [ ] **Prerequisite for GRAPHOS-A2A-R002** (owner repo: agent-utilities):
  `WorkItemA2AAuthority._enqueue`/`dispatch` in `graph_os/a2a/authority.py`
  refuse every request carrying `decision.selected_tools` because AU's
  dispatch carrier (`agent_utilities.orchestration.agent_dispatch.
  AgentTurnEnvelope` / `enqueue_agent_turn`) has no field or mechanism to
  carry and enforce a caller-filtered tool allowlist at execution time. AU
  must add that carried, enforced tool-subset field before R002's
  caller-filtered exposure can be implemented here.
- [x] Add a release-canary check proving the promoted environment serves the
  FastMCP major it declares (GRAPHOS-A2A-R004). Semantic/composition proof
  that FastMCP 4 runs end to end remains open.
