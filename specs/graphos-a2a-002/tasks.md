# A2A context-budget tool-subset admission — tasks

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. See [design](plan.md) and [tests](test-spec.md).

- [x] Open this spec, linking EG's `AgentAssemble` owner (`EG-DECISION-ENGINE-001`/`EG-DECISION-ENGINE-R036`) and AU's signed envelope owner (`AU-CONTROL-001`) by stable ID.
- [x] Open the AU owner row this door depends on: `AU-CONTROL-R035`, the signed `AgentTurnEnvelope`'s missing allowed-tool-subset field (agent-utilities PR #135, merged).
- [x] Add the typed `ToolSubsetAdmissionRequest`/`ToolSubsetAdmissionDecision` models (`graph_os/a2a/admission.py`), reusing the existing `_WireModel` base and `A2AContextBudget` (`GRAPHOS-A2A-002-R001`).
- [x] Add `admit_tool_subset`, returning today's exact fail-closed `EG_ASSEMBLE_UNAVAILABLE` refusal (`GRAPHOS-A2A-002-R001.1`).
- [x] Add the refusal unit test (`tests/a2a/test_admission.py`) proving the exact fields of that result (`GRAPHOS-A2A-002-R001.1`, `GRAPHOS-A2A-002-R004`).
- [ ] **Prerequisite (owner repo: epistemic-graph):** `AgentAssemble` must stop returning unavailable before `admit_tool_subset` can derive a real subset (`GRAPHOS-A2A-002-R002`).
- [ ] **Prerequisite (owner repo: agent-utilities, opened as `AU-CONTROL-R035`):** the signed `AgentTurnEnvelope` must carry and let a harness enforce an allowed-tool-subset field before an admitted decision can be cryptographically carried (`GRAPHOS-A2A-002-R003`).
- [ ] Once both prerequisites land: wire `admit_tool_subset` to the real EG assembly client and the signed envelope, add the admitted-path and partial/stale-refusal tests in [test-spec.md](test-spec.md), and replace `OrchestratorA2ARouter`'s and `WorkItemA2AAuthority`'s inline `A2AAssemblyUnavailable` raises with a call to this one admission authority.
