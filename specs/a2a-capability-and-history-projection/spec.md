# GRAPHOS-A2A — A2A streaming, push notifications and transition-history projection

## Status legend

Status terms (`READY FOR IMPLEMENTATION`/`BUILDING`/`SOURCE LANDED`/`ACCEPTED`) and acceptance evidence rules are defined once by [GRAPHOS-A2A](../a2a-task-projection/spec.md#status-legend); this spec's `ACCEPTED` additionally requires the streaming/push/history served-path evidence in [test-spec.md](test-spec.md).

## User stories and scope

An A2A caller needs to know, truthfully, whether this GraphOS deployment can stream task updates, deliver push notifications on task state changes, and answer "what happened to this task and when" — before it relies on any of those. GraphOS is the process and public door: it may advertise a capability on the Agent Card only when it can actually serve it. The underlying work is agent-utilities' WorkItem event stream (agent decisions, claims, progress and terminal outcomes) and epistemic-graph's durable, append-only transition history (the system of record for that WorkItem's full state-change ledger). GraphOS projects both through the A2A facade; it does not become a second authority for either.

Depends on [`GRAPHOS-A2A`](../a2a-task-projection/spec.md) (the context-budget subset of the A2A task projection) landing first: this spec's streaming and push projections reuse the same bounded event-cursor and context-budget machinery GRAPHOS-A2A introduces, rather than inventing a second one.

GraphOS owns the transport, authenticated Agent Card projection and typed refusal. [agent-utilities](https://github.com/Knuckles-Team/agent-utilities) (`AU-CONTROL-001`) owns the WorkItem event stream — launch, step, tool, usage, artifact, receipt and terminal events — that this spec projects as A2A task updates and push triggers. [epistemic-graph](https://github.com/Knuckles-Team/epistemic-graph) (`EG-DURABLE-KERNEL`) owns the durable, append-only transition-history ledger this spec projects as `tasks/get` history. GraphOS must not persist a second WorkItem event log, a second transition-history ledger, or synthesize history from in-process state when the durable authority is unavailable.

## Requirements

- **A2A-H01 Advertise only what is wired.** The Agent Card's `capabilities.streaming`, `capabilities.pushNotifications`, and `capabilities.stateTransitionHistory` fields are each `true` only when a real durable authority object for that capability is wired into the running composition (`A2AStreamingAuthority`, `A2APushNotificationAuthority`, `A2ATransitionHistoryAuthority` per `graph_os/a2a/models.py`), and `false` otherwise. No configuration flag, environment variable, or partial/mocked authority can force a field `true` on its own.
- **A2A-H02 WorkItem stream projection, not a shadow log.** `message/stream` and push-notification delivery read directly from agent-utilities' `AU-CONTROL-001` WorkItem event stream (via its public port) for task status, step, and terminal-outcome updates. GraphOS stores no independent copy of that event log; a projection gap (missing or stale event) is surfaced as a typed `UNAVAILABLE`, never backfilled from local state.
- **A2A-H03 Transition history is read-through to epistemic-graph.** `tasks/get` with history requested reads the task's full state-transition ledger through to epistemic-graph's `EG-DURABLE-KERNEL` durable authority. GraphOS performs no local reconstruction of history from recent events; the response is exactly what the durable ledger returns, or the refusal in A2A-H04.
- **A2A-H04 Typed refusal when the durable history authority is absent.** When no `A2ATransitionHistoryAuthority` is wired, or the wired authority is unreachable, GraphOS returns a distinct JSON-RPC error `A2ATransitionHistoryUnavailable` (reusing the `-3201x` block introduced by `GRAPHOS-A2A-R001.1`'s `-32010`) with a privacy-safe message; it never returns an empty history, a partial in-memory reconstruction, or a generic method-not-found.
- **A2A-H05 Push notifications are best-effort over a durable outbox.** Push delivery is retried from epistemic-graph's durable outbox (`EG-DURABLE-KERNEL-R001` head-only retry/dead-letter semantics), never from an in-process queue that is lost on restart. A push target that repeatedly fails is dead-lettered and reported, not silently dropped.
- **A2A-H06 No capability advertised before GRAPHOS-A2A context-budget lands.** Until `GRAPHOS-A2A`'s context-budget subset is on the default branch, the three capability fields in A2A-H01 stay fixed `false` regardless of any authority object passed in; the composition layer refuses to wire a real authority ahead of its dependency.
- **A2A-H07 Caller-visible card matches served behavior at every revision.** A contract test asserts that whichever subset of streaming/push/history is actually exercised by the integration fixture at the exact tested revision matches the Agent Card's advertised capability subset — no field is ever `true` while its serving path is absent, and no field is `false` while its serving path is demonstrably live.

## Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-A2A-R001.1 (this repo, landed) | Typed capability-advertisement model refusing untruthful advertisement | LANDED (`R001.1` slice, this PR) |
| AU-CONTROL-R001 (agent-utilities) | WorkItem event stream this spec projects, not re-implements | EXTERNAL — see AU's `specs/agent-control-plane` |
| EG-DURABLE-KERNEL-R001, R028 (epistemic-graph) | Durable transition-history ledger and outbox retry/dead-letter this spec reads through to | EXTERNAL — see EG's `specs/durable-graph-kernel` |
| GRAPHOS-A2A (this repo, dependency) | Context-budget subset this spec's streaming/push cursor machinery reuses | NOT LANDED — blocks A2A-H06 |

## Acceptance

1. A fresh public checkout with no authorities wired serves an Agent Card with all three capability fields `false`, and `tasks/get` history requests and `message/stream` both return the typed refusal in A2A-H04, never a fabricated answer.
2. With a fixture `A2ATransitionHistoryAuthority` and `A2AStreamingAuthority` wired, the same checkout advertises exactly those two fields `true`, `push_notifications` stays `false`, and `tasks/get` history and `message/stream` serve real projected data traceable to the AU/EG fixtures.
3. Quality gates pass at the exact default-branch revision with no duplicate event log, no local history reconstruction, and no suppression of the new refusal's negative tests.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
