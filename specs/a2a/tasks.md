# GRAPHOS-A2A — tasks

## A2A task and approval projection
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

### Decomposition children (tracked)

- [ ] **GRAPHOS-A2A-R002.1:** Fail-closed refusal when assembly selects a tool subset
- [ ] **GRAPHOS-A2A-R002.2:** Caller-filtered tool-subset enforcement at dispatch
- [ ] **GRAPHOS-A2A-R002.2.1:** Remaining scope of GRAPHOS-A2A-R002.2 (slice .1): Caller-filtered tool-subset enforcement at dispatch
- [ ] **GRAPHOS-A2A-R005:** Durable, fail-closed A2A approval exchange for tool calls
- [ ] **GRAPHOS-A2A-R005.1:** Fail-closed `graphos.plan/confirm` stub on the A2A method table
- [ ] **GRAPHOS-A2A-R005.2:** Full signed human approval chain and input-required task emission
- [ ] **GRAPHOS-A2A-R005.2.1:** Remaining scope of GRAPHOS-A2A-R005.2 (slice .1): Full signed human approval chain and input-required task emission
- [ ] **GRAPHOS-A2A-R006:** Operation invoke and plan-confirm methods for A2A
- [ ] **GRAPHOS-A2A-R006.1:** Register `graphos.op/invoke` and `graphos.plan/confirm`, fail-closed
- [ ] **GRAPHOS-A2A-R006.2:** Bridge `graphos.op/invoke` to the shared hosted-operation registry
- [x] **GRAPHOS-A2A-R002.1:** Fail-closed refusal when assembly selects a tool subset
- [ ] **GRAPHOS-A2A-R002.2:** Caller-filtered tool-subset enforcement at dispatch
- [ ] **GRAPHOS-A2A-R002.2.1:** Remaining scope of GRAPHOS-A2A-R002.2 (slice .1): Caller-filtered tool-subset enforcement at dispatch
- [ ] **GRAPHOS-A2A-R005:** Durable, fail-closed A2A approval exchange for tool calls
- [x] **GRAPHOS-A2A-R005.1:** Fail-closed graphos.plan/confirm stub on the A2A method table
- [ ] **GRAPHOS-A2A-R005.2:** Full signed human approval chain and input-required task emission
- [ ] **GRAPHOS-A2A-R005.2.1:** Remaining scope of GRAPHOS-A2A-R005.2 (slice .1): Full signed human approval chain and input-required task emission
- [ ] **GRAPHOS-A2A-R006:** Operation invoke and plan-confirm methods for A2A
- [x] **GRAPHOS-A2A-R006.1:** Register graphos.op/invoke and graphos.plan/confirm, fail-closed
- [ ] **GRAPHOS-A2A-R006.2:** Bridge graphos.op/invoke to the shared hosted-operation registry
- [ ] **GRAPHOS-A2A-R006.2.1:** Remaining scope of GRAPHOS-A2A-R006.2 (slice .1): Bridge graphos.op/invoke to the shared hosted-operation registry

## A2A streaming, push notifications and transition-history projection
Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED** except `GRAPHOS-A2A-R009.1`. See [design](plan.md) and [tests](test-spec.md).

- [x] Define `A2AAgentCapabilities.from_wired_authorities` plus `A2AStreamingAuthority`/`A2APushNotificationAuthority`/`A2ATransitionHistoryAuthority` runtime-checkable protocols in `graph_os/a2a/models.py` (`GRAPHOS-A2A-R009`, `GRAPHOS-A2A-R009.1`). Default capabilities stay all-false; a non-conforming authority object raises `TypeError` instead of being silently accepted. Proven by `tests/a2a/test_models.py`.
- [ ] Wait for `GRAPHOS-A2A` (context-budget subset) to land; do not implement the streaming cursor or push-trigger machinery ahead of it (`GRAPHOS-A2A-R014`).
- [ ] Implement `message/stream` reading agent-utilities' WorkItem event stream (`AU-CONTROL-001`) through its public port; no local event-log copy (`GRAPHOS-A2A-R010`).
- [ ] Implement `tasks/get` history read-through to epistemic-graph's durable kernel (`EG-DURABLE-KERNEL`) (`GRAPHOS-A2A-R011`).
- [ ] Add the `A2ATransitionHistoryUnavailable` typed refusal for an absent/unreachable history authority (`GRAPHOS-A2A-R012`).
  - [x] `GRAPHOS-A2A-R012.1`: landed inventory only — the protocol exists, the error type does not. Proven by `tests/a2a/test_models.py`.
  - [ ] `GRAPHOS-A2A-R012.2`: add the `A2ATransitionHistoryUnavailable` error type to `graph_os/a2a/authority.py`.
  - [ ] `GRAPHOS-A2A-R012.3`: wire `tasks/get`'s refusal (`A2A-HT06`); blocked on `GRAPHOS-A2A` (`GRAPHOS-A2A-R014`).
- [ ] Implement push-notification delivery over epistemic-graph's durable outbox with dead-letter reporting (`GRAPHOS-A2A-R013`).
- [ ] Wire real authorities into composition only after each path is proven; run all test-spec rows; record exact revision evidence before flipping any capability field `true` and marking its requirement accepted (`GRAPHOS-A2A-R015`).

## A2A context-budget tool-subset admission
Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. See [design](plan.md) and [tests](test-spec.md).

- [x] Open this spec, linking EG's `AgentAssemble` owner (`EG-DECISION-ENGINE-001`/`EG-DECISION-ENGINE-R036`) and AU's signed envelope owner (`AU-CONTROL-001`) by stable ID.
- [x] Open the AU owner row this door depends on: `AU-CONTROL-R035`, the signed `AgentTurnEnvelope`'s missing allowed-tool-subset field (agent-utilities PR #135, merged).
- [x] Add the typed `ToolSubsetAdmissionRequest`/`ToolSubsetAdmissionDecision` models (`graph_os/a2a/admission.py`), reusing the existing `_WireModel` base and `A2AContextBudget` (`GRAPHOS-A2A-R016`).
- [x] Add `admit_tool_subset`, returning today's exact fail-closed `EG_ASSEMBLE_UNAVAILABLE` refusal (`GRAPHOS-A2A-R016.1`).
- [x] Add the refusal unit test (`tests/a2a/test_admission.py`) proving the exact fields of that result (`GRAPHOS-A2A-R016.1`, `GRAPHOS-A2A-R019`).
- [ ] **Prerequisite (owner repo: epistemic-graph):** `AgentAssemble` must stop returning unavailable before `admit_tool_subset` can derive a real subset (`GRAPHOS-A2A-R017`).
- [ ] **Prerequisite (owner repo: agent-utilities, opened as `AU-CONTROL-R035`):** the signed `AgentTurnEnvelope` must carry and let a harness enforce an allowed-tool-subset field before an admitted decision can be cryptographically carried (`GRAPHOS-A2A-R018`).
- [ ] Once both prerequisites land: wire `admit_tool_subset` to the real EG assembly client and the signed envelope, add the admitted-path and partial/stale-refusal tests in [test-spec.md](test-spec.md), and replace `OrchestratorA2ARouter`'s and `WorkItemA2AAuthority`'s inline `A2AAssemblyUnavailable` raises with a call to this one admission authority.
