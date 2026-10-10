# A2A context-budget tool-subset admission

## Status legend

`READY FOR IMPLEMENTATION` describes this build contract, not working code. `BUILDING` means source is in progress. `SOURCE LANDED` means it is on the default branch. `ACCEPTED` requires exact default-branch contract and test evidence from [test-spec.md](test-spec.md). Do not infer acceptance from a focused unit test alone.

## User stories and scope

An authenticated A2A caller submits a task whose execution needs more than the trivially routed single agent: a selected agent graph whose tools must fit inside the caller's declared context budget. GraphOS owns the admission door for that request — the smallest authorized tool subset that covers the selected agent graph within the caller's `contextBudgetTokens` — and must decide to admit or refuse it with a typed, provenanced result. GraphOS does not solve capability assignment itself, does not invent a local heuristic or cached substitute for the real assembly answer, and does not let an unenforceable selection reach execution.

Two other owners' public contracts are load-bearing and are linked here by stable ID, never copied:

- **epistemic-graph** owns agent/tool assembly and the `AgentAssemble` operation that derives a legal tool subset for a capability-constrained agent graph: [`EG-DECISION-ENGINE-001`](https://github.com/Knuckles-Team/epistemic-graph/blob/main/specs/decision-engine/spec.md), requirement `EG-DECISION-ENGINE-R036`.
- **agent-utilities** owns the signed dispatch carrier that must cryptographically bind and let a harness enforce the admitted subset: [`AU-CONTROL-001`](https://github.com/Knuckles-Team/agent-utilities/blob/main/specs/agent-control-plane/spec.md), requirement `AU-CONTROL-R035`.

GraphOS owns admission, refusal, and provenance at the door: given a request naming a selected agent graph and a context budget, it returns either an admitted tool subset grounded in a committed EG assembly answer and carried in a signed AU envelope, or a typed, provenanced refusal. Today both upstream seams are incomplete — EG's `AgentAssemble` returns unavailable and AU's `AgentTurnEnvelope` has no allowed-tool-subset field (`AU-CONTROL-R035`, opened by this spec) — so this door must fail closed rather than approximate, cache, or silently widen the exposed tool surface.

## Requirements

- **GRAPHOS-A2A-002-R001 Typed admission request/decision model.** GraphOS defines a typed `ToolSubsetAdmissionRequest` (selected agent graph reference, caller context budget) and `ToolSubsetAdmissionDecision` (admitted flag, admitted tool subset, typed refusal reason, provenance) at the A2A door, independent of any specific upstream wiring. Split into `GRAPHOS-A2A-002-R001.1`, the model plus a refusal test proving today's exact fail-closed result, with the live EG/AU wiring remaining open under the parent.
- **GRAPHOS-A2A-002-R002 Context-budget-bound subset sourced from EG assembly.** The admission door derives the admitted tool subset only from a committed epistemic-graph `AgentAssemble` answer (`EG-DECISION-ENGINE-R036`) covering the selected agent graph within the caller's context budget. While `AgentAssemble` returns unavailable, admission refuses closed with reason `EG_ASSEMBLE_UNAVAILABLE` and never derives a subset by local heuristic, prior cached result, or partial coverage.
- **GRAPHOS-A2A-002-R003 Cryptographic enforcement via the signed AU envelope.** An admitted tool subset is carried only inside the signed AU `AgentTurnEnvelope`'s allowed-tool-subset field (`AU-CONTROL-R035`) so a harness enforces it at execution time rather than treating it as advisory. While that field does not exist in the signed envelope, admission refuses closed with reason `ENVELOPE_LACKS_ALLOWED_TOOL_SUBSET` even if an EG assembly answer exists, and never dispatches an unenforceable subset.
- **GRAPHOS-A2A-002-R004 Fail-closed refusal with typed reason and provenance.** Every admission decision, admitted or refused, carries a stable machine-readable reason (for a refusal) or provenance reference (for an admission) sufficient to audit exactly which upstream answer authorized it. A request is never admitted on the basis of an absent, errored, or timed-out upstream answer.
- **GRAPHOS-A2A-002-R005 No advisory, cached, or partial substitute.** The admission door never returns a tool subset that is advisory-only, drawn from a prior request's cached answer, or a partial/best-effort coverage of the selected agent graph. An incomplete upstream answer is a refusal, not a smaller admission.
- **GRAPHOS-A2A-002-R006 Fresh checkout.** Contract and unit tests for the admission model and its refusal behavior run with published packages and no private network, sibling checkout, or live production identity provider, so an external PR reviewer can run them unmodified.

## Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-A2A-002-R001, GRAPHOS-A2A-002-R001.1 | Typed admission request/decision model and its fail-closed refusal test | SPECIFIED |
| GRAPHOS-A2A-002-R002 | Context-budget-bound subset sourced only from committed EG `AgentAssemble` | SPECIFIED (blocked on EG-DECISION-ENGINE-R036) |
| GRAPHOS-A2A-002-R003 | Enforcement via the signed AU envelope's allowed-tool-subset field | SPECIFIED (blocked on AU-CONTROL-R035) |
| GRAPHOS-A2A-002-R004 | Typed, provenanced result for every decision | SPECIFIED |
| GRAPHOS-A2A-002-R005 | No advisory/cached/partial substitute ever admitted | SPECIFIED |
| GRAPHOS-A2A-002-R006 | Fresh-checkout contract proof | SPECIFIED |

## Acceptance

1. A fresh public checkout runs the admission model's unit and refusal tests with no private dependency.
2. Given today's unavailable `AgentAssemble` and tool-subset-less `AgentTurnEnvelope`, every admission request returns the exact typed refusal in [test-spec.md](test-spec.md); no request is ever admitted on an absent upstream answer.
3. Once EG `AgentAssemble` and AU's envelope field land, an admitted decision traces to one committed EG assembly answer and is carried only inside the signed envelope's allowed-tool-subset field; no other path can authorize exposure.
4. Quality gates pass at the exact default-branch revision with no duplicate admission authority, local heuristic fallback, or scanner suppression.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
