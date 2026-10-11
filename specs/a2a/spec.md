# GRAPHOS-A2A — A2A projection, streaming, history and tool-subset admission

## A2A task and approval projection
### Status legend

`READY FOR IMPLEMENTATION` describes this build contract, not working code. `BUILDING` means source is in progress. `SOURCE LANDED` means it is on the default branch. `ACCEPTED` requires exact default-branch contract, security, and served-path evidence from [test-spec.md](test-spec.md). Do not infer acceptance from a focused test or an A2A card alone.

### User stories and scope

An authenticated A2A caller can discover permitted agent skills, submit a task, get/list/cancel it, and resume a stream without creating a second task store. When an agent needs a human to approve a tool effect, the original human can review an `input-required` task and respond with a signed, single-use confirmation. A caller can invoke a GraphOS operation through A2A with the same scope, policy, effect and error semantics as MCP and HTTP.

GraphOS owns the transport, authenticated projection, operation adapter and served composition. The durable WorkItem/task state, human grant and atomic fences belong to the public engine and agent orchestration contracts. GraphOS must not persist a second task history, mint a human identity from a service token, or accept an unverified approval claim.

### Requirements

- **A2A-01 Native task methods.** Serve authenticated `/.well-known/agent-card.json` and JSON-RPC `/a2a` methods `message/send`, `message/stream`, `tasks/get`, `tasks/list`, `tasks/cancel`, and `tasks/resubscribe`. `message/send` requires an idempotency key; task IDs are opaque and tenant-bound. Get/list/cancel enforce the caller's durable authority. Stream/resubscribe use a bounded cursor and return ordered durable events, including terminal state, without lossy in-process task state.
- **A2A-02 Assembly and routing.** Incoming task dispatch uses the existing agent orchestration public port and an engine-backed WorkItem. Agent/capability selection uses caller-filtered assembly with budgeted tool subsets. The A2A task ID maps to the same durable parent/child WorkItem identities used by the dispatcher; routing ambiguity or missing public contract fails closed. The Agent Card advertises only currently usable, caller-authorized skills and methods.
- **A2A-03 Operation projection.** `graphos.op/invoke {op, params, plan_ref?, idempotency_key?}` passes the verified A2A caller to the hosted operation registry's `invoke` path with `Surface.A2A`. The exact operation scope, Eunomia, subject, effect, reservation, audit and error envelope are reused. Bespoke A2A elevation or direct child-tool execution is retired after equivalent ops are available.
- **A2A-04 Approval request.** A pending effect that needs `Confirm.PLAN` moves its durable task to `input-required` and emits `{task_id, pending_call_id, plan_ref, preview, expiry}` with no bearer material or raw secret params. The pending call and parent/child WorkItem relation are server-attested and durable across pause, restart and replica change. Console-class effects return `STEP_UP_REQUIRED` and a console URL; A2A cannot confirm them.
- **A2A-05 Approval response.** `graphos.plan/confirm` is a signed message from an authenticated human session. It must prove the original eligible human's identity and tenant, the pending call, parent/child task relation, operation and parameter digest, policy and registry revisions, unexpired plan, and a revocable human grant. An agent or service token alone cannot stand in for the human. One atomic compare-and-set consumes the grant/plan immediately before the effect; replay, changed params, different human, expired/revoked grant, canceled task, and stale policy fail without effect. Pending approval remains disabled until this full chain is proven end to end.
- **A2A-06 Failure and receipts.** Unknown methods return JSON-RPC method-not-found; malformed input returns a privacy-safe validation error; missing auth or scope, idempotency conflict, unavailable assembly, stale plan, and uncertain effect return stable distinct codes. Cancellation never claims to undo an already committed external effect. Every governed effect has durable audit reservation and linked outcome; a post-effect audit failure is indeterminate and reconciled.
- **A2A-07 Fresh checkout.** Contract and integration tests run with published packages, local fixture identities and ephemeral local services. No private network, sibling checkout or live production identity provider is required to review an external PR.

### Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-A2A-R001, GRAPHOS-A2A-R008 | First-party native A2A facade over public engine and agent ports | NOT ACCEPTED |
| GRAPHOS-A2A-R002 | Authenticated inbound routing and caller-filtered assembly/tool subset | NOT ACCEPTED |
| GRAPHOS-A2A-R005 | Durable human tool-call approval exchange and fail-closed activation | NOT ACCEPTED |
| GRAPHOS-A2A-R006 | `graphos.op/invoke`, `graphos.plan/confirm`, shared operation registry and error projection | NOT ACCEPTED |
| GRAPHOS-A2A-R003, GRAPHOS-A2A-R004 | No fleet skill/prompt harvest duplicate; FastMCP 4 served bridge and one owner loop | NOT ACCEPTED |
| GRAPHOS-A2A-R007 | Pre-effect audit reservation and outcome linkage for approved effects | NOT ACCEPTED |

### Acceptance

1. A fresh public checkout boots A2A using published engine, agent and connector contracts and can run a unary task, durable stream/resubscribe and cancel/get/list against an ephemeral local stack.
2. A representative operation has identical authorization and error result through A2A, MCP and HTTP. A loaded child tool and A2A operation share one policy/audit path.
3. Human approval succeeds once across a pause/restart/replica boundary, and every negative case in [test-spec.md](test-spec.md) proves zero effect. The feature stays unavailable until that proof exists.
4. Quality gates pass at the exact default-branch revision with no duplicate task store, authority shim, private dependency, or scanner suppression.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.

## A2A streaming, push notifications and transition-history projection
### Status legend

Status terms (`READY FOR IMPLEMENTATION`/`BUILDING`/`SOURCE LANDED`/`ACCEPTED`) and acceptance evidence rules are defined once by [GRAPHOS-A2A](#status-legend); this spec's `ACCEPTED` additionally requires the streaming/push/history served-path evidence in [test-spec.md](test-spec.md).

### User stories and scope

An A2A caller needs to know, truthfully, whether this GraphOS deployment can stream task updates, deliver push notifications on task state changes, and answer "what happened to this task and when" — before it relies on any of those. GraphOS is the process and public door: it may advertise a capability on the Agent Card only when it can actually serve it. The underlying work is agent-utilities' WorkItem event stream (agent decisions, claims, progress and terminal outcomes) and epistemic-graph's durable, append-only transition history (the system of record for that WorkItem's full state-change ledger). GraphOS projects both through the A2A facade; it does not become a second authority for either.

Depends on [`GRAPHOS-A2A`](spec.md) (the context-budget subset of the A2A task projection) landing first: this spec's streaming and push projections reuse the same bounded event-cursor and context-budget machinery GRAPHOS-A2A introduces, rather than inventing a second one.

GraphOS owns the transport, authenticated Agent Card projection and typed refusal. [agent-utilities](https://github.com/Knuckles-Team/agent-utilities) (`AU-CONTROL-001`) owns the WorkItem event stream — launch, step, tool, usage, artifact, receipt and terminal events — that this spec projects as A2A task updates and push triggers. [epistemic-graph](https://github.com/Knuckles-Team/epistemic-graph) (`EG-DURABLE-KERNEL`) owns the durable, append-only transition-history ledger this spec projects as `tasks/get` history. GraphOS must not persist a second WorkItem event log, a second transition-history ledger, or synthesize history from in-process state when the durable authority is unavailable.

### Requirements

- **A2A-H01 Advertise only what is wired.** The Agent Card's `capabilities.streaming`, `capabilities.pushNotifications`, and `capabilities.stateTransitionHistory` fields are each `true` only when a real durable authority object for that capability is wired into the running composition (`A2AStreamingAuthority`, `A2APushNotificationAuthority`, `A2ATransitionHistoryAuthority` per `graph_os/a2a/models.py`), and `false` otherwise. No configuration flag, environment variable, or partial/mocked authority can force a field `true` on its own.
- **A2A-H02 WorkItem stream projection, not a shadow log.** `message/stream` and push-notification delivery read directly from agent-utilities' `AU-CONTROL-001` WorkItem event stream (via its public port) for task status, step, and terminal-outcome updates. GraphOS stores no independent copy of that event log; a projection gap (missing or stale event) is surfaced as a typed `UNAVAILABLE`, never backfilled from local state.
- **A2A-H03 Transition history is read-through to epistemic-graph.** `tasks/get` with history requested reads the task's full state-transition ledger through to epistemic-graph's `EG-DURABLE-KERNEL` durable authority. GraphOS performs no local reconstruction of history from recent events; the response is exactly what the durable ledger returns, or the refusal in A2A-H04.
- **A2A-H04 Typed refusal when the durable history authority is absent.** When no `A2ATransitionHistoryAuthority` is wired, or the wired authority is unreachable, GraphOS returns a distinct JSON-RPC error `A2ATransitionHistoryUnavailable` (reusing the `-3201x` block introduced by `GRAPHOS-A2A-R009.1`'s `-32010`) with a privacy-safe message; it never returns an empty history, a partial in-memory reconstruction, or a generic method-not-found.
- **A2A-H05 Push notifications are best-effort over a durable outbox.** Push delivery is retried from epistemic-graph's durable outbox (`EG-DURABLE-KERNEL-R001` head-only retry/dead-letter semantics), never from an in-process queue that is lost on restart. A push target that repeatedly fails is dead-lettered and reported, not silently dropped.
- **A2A-H06 No capability advertised before GRAPHOS-A2A context-budget lands.** Until `GRAPHOS-A2A`'s context-budget subset is on the default branch, the three capability fields in A2A-H01 stay fixed `false` regardless of any authority object passed in; the composition layer refuses to wire a real authority ahead of its dependency.
- **A2A-H07 Caller-visible card matches served behavior at every revision.** A contract test asserts that whichever subset of streaming/push/history is actually exercised by the integration fixture at the exact tested revision matches the Agent Card's advertised capability subset — no field is ever `true` while its serving path is absent, and no field is `false` while its serving path is demonstrably live.

### Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-A2A-R009.1 (this repo, landed) | Typed capability-advertisement model refusing untruthful advertisement | LANDED (`R001.1` slice, this PR) |
| AU-CONTROL-R001 (agent-utilities) | WorkItem event stream this spec projects, not re-implements | EXTERNAL — see AU's `specs/agent-control-plane` |
| EG-DURABLE-KERNEL-R001, R028 (epistemic-graph) | Durable transition-history ledger and outbox retry/dead-letter this spec reads through to | EXTERNAL — see EG's `specs/durable-graph-kernel` |
| GRAPHOS-A2A (this repo, dependency) | Context-budget subset this spec's streaming/push cursor machinery reuses | NOT LANDED — blocks A2A-H06 |

### Acceptance

1. A fresh public checkout with no authorities wired serves an Agent Card with all three capability fields `false`, and `tasks/get` history requests and `message/stream` both return the typed refusal in A2A-H04, never a fabricated answer.
2. With a fixture `A2ATransitionHistoryAuthority` and `A2AStreamingAuthority` wired, the same checkout advertises exactly those two fields `true`, `push_notifications` stays `false`, and `tasks/get` history and `message/stream` serve real projected data traceable to the AU/EG fixtures.
3. Quality gates pass at the exact default-branch revision with no duplicate event log, no local history reconstruction, and no suppression of the new refusal's negative tests.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.

## A2A context-budget tool-subset admission
### Status legend

`READY FOR IMPLEMENTATION` describes this build contract, not working code. `BUILDING` means source is in progress. `SOURCE LANDED` means it is on the default branch. `ACCEPTED` requires exact default-branch contract and test evidence from [test-spec.md](test-spec.md). Do not infer acceptance from a focused unit test alone.

### User stories and scope

An authenticated A2A caller submits a task whose execution needs more than the trivially routed single agent: a selected agent graph whose tools must fit inside the caller's declared context budget. GraphOS owns the admission door for that request — the smallest authorized tool subset that covers the selected agent graph within the caller's `contextBudgetTokens` — and must decide to admit or refuse it with a typed, provenanced result. GraphOS does not solve capability assignment itself, does not invent a local heuristic or cached substitute for the real assembly answer, and does not let an unenforceable selection reach execution.

Two other owners' public contracts are load-bearing and are linked here by stable ID, never copied:

- **epistemic-graph** owns agent/tool assembly and the `AgentAssemble` operation that derives a legal tool subset for a capability-constrained agent graph: [`EG-DECISION-ENGINE-001`](https://github.com/Knuckles-Team/epistemic-graph/blob/main/specs/decision-engine/spec.md), requirement `EG-DECISION-ENGINE-R036`.
- **agent-utilities** owns the signed dispatch carrier that must cryptographically bind and let a harness enforce the admitted subset: [`AU-CONTROL-001`](https://github.com/Knuckles-Team/agent-utilities/blob/main/specs/agent-control-plane/spec.md), requirement `AU-CONTROL-R035`.

GraphOS owns admission, refusal, and provenance at the door: given a request naming a selected agent graph and a context budget, it returns either an admitted tool subset grounded in a committed EG assembly answer and carried in a signed AU envelope, or a typed, provenanced refusal. Today both upstream seams are incomplete — EG's `AgentAssemble` returns unavailable and AU's `AgentTurnEnvelope` has no allowed-tool-subset field (`AU-CONTROL-R035`, opened by this spec) — so this door must fail closed rather than approximate, cache, or silently widen the exposed tool surface.

### Requirements

- **GRAPHOS-A2A-R016 Typed admission request/decision model.** GraphOS defines a typed `ToolSubsetAdmissionRequest` (selected agent graph reference, caller context budget) and `ToolSubsetAdmissionDecision` (admitted flag, admitted tool subset, typed refusal reason, provenance) at the A2A door, independent of any specific upstream wiring. Split into `GRAPHOS-A2A-R016.1`, the model plus a refusal test proving today's exact fail-closed result, with the live EG/AU wiring remaining open under the parent.
- **GRAPHOS-A2A-R017 Context-budget-bound subset sourced from EG assembly.** The admission door derives the admitted tool subset only from a committed epistemic-graph `AgentAssemble` answer (`EG-DECISION-ENGINE-R036`) covering the selected agent graph within the caller's context budget. While `AgentAssemble` returns unavailable, admission refuses closed with reason `EG_ASSEMBLE_UNAVAILABLE` and never derives a subset by local heuristic, prior cached result, or partial coverage.
- **GRAPHOS-A2A-R018 Cryptographic enforcement via the signed AU envelope.** An admitted tool subset is carried only inside the signed AU `AgentTurnEnvelope`'s allowed-tool-subset field (`AU-CONTROL-R035`) so a harness enforces it at execution time rather than treating it as advisory. While that field does not exist in the signed envelope, admission refuses closed with reason `ENVELOPE_LACKS_ALLOWED_TOOL_SUBSET` even if an EG assembly answer exists, and never dispatches an unenforceable subset.
- **GRAPHOS-A2A-R019 Fail-closed refusal with typed reason and provenance.** Every admission decision, admitted or refused, carries a stable machine-readable reason (for a refusal) or provenance reference (for an admission) sufficient to audit exactly which upstream answer authorized it. A request is never admitted on the basis of an absent, errored, or timed-out upstream answer.
- **GRAPHOS-A2A-R020 No advisory, cached, or partial substitute.** The admission door never returns a tool subset that is advisory-only, drawn from a prior request's cached answer, or a partial/best-effort coverage of the selected agent graph. An incomplete upstream answer is a refusal, not a smaller admission.
- **GRAPHOS-A2A-R021 Fresh checkout.** Contract and unit tests for the admission model and its refusal behavior run with published packages and no private network, sibling checkout, or live production identity provider, so an external PR reviewer can run them unmodified.

### Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-A2A-R016, GRAPHOS-A2A-R016.1 | Typed admission request/decision model and its fail-closed refusal test | SPECIFIED |
| GRAPHOS-A2A-R017 | Context-budget-bound subset sourced only from committed EG `AgentAssemble` | SPECIFIED (blocked on EG-DECISION-ENGINE-R036) |
| GRAPHOS-A2A-R018 | Enforcement via the signed AU envelope's allowed-tool-subset field | SPECIFIED (blocked on AU-CONTROL-R035) |
| GRAPHOS-A2A-R019 | Typed, provenanced result for every decision | SPECIFIED |
| GRAPHOS-A2A-R020 | No advisory/cached/partial substitute ever admitted | SPECIFIED |
| GRAPHOS-A2A-R021 | Fresh-checkout contract proof | SPECIFIED |

### Acceptance

1. A fresh public checkout runs the admission model's unit and refusal tests with no private dependency.
2. Given today's unavailable `AgentAssemble` and tool-subset-less `AgentTurnEnvelope`, every admission request returns the exact typed refusal in [test-spec.md](test-spec.md); no request is ever admitted on an absent upstream answer.
3. Once EG `AgentAssemble` and AU's envelope field land, an admitted decision traces to one committed EG assembly answer and is carried only inside the signed envelope's allowed-tool-subset field; no other path can authorize exposure.
4. Quality gates pass at the exact default-branch revision with no duplicate admission authority, local heuristic fallback, or scanner suppression.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
