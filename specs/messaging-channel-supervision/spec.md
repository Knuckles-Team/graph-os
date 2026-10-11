# GRAPHOS-MESSAGING — Messaging channel supervision lifecycle

## Status legend

`READY FOR IMPLEMENTATION` describes this build contract, not working code. `BUILDING` means source is in progress. `SOURCE LANDED` means it is on the default branch. `ACCEPTED` requires exact default-branch contract and served-path evidence from [test-spec.md](test-spec.md). Do not infer acceptance from a focused test alone.

## User stories and scope

As the GraphOS process, I supervise every registered messaging channel process (telegram, mattermost, and any further `agent_utilities.messaging` entry-point backend) through one typed lifecycle: start, health-gated activation, bounded-backoff restart on a recoverable failure, graceful stop with drain, and an explicit degraded state when a configured channel's adapter is missing or fails to instantiate. As an operator, I can read a channel's current supervision state and know it reflects reality rather than a log line I have to grep for.

This pulls the channel supervision lifecycle out of [`GRAPHOS-HOST-R011`](../host-composition-boundary/requirements.md) (host-composition-boundary), which states only that GraphOS "serves the messaging channel adapters, listener, service, daemon, polling, reaper and host routing" without naming the lifecycle's states, transitions, or backoff bounds. `GRAPHOS-HOST-R011`'s row is kept; the requirements below are its superseding detail for the supervision slice only, and are marked `superseded-by` on that row.

Org boundary: GraphOS supervises the channel processes — `graph_os.messaging.router.InboundRouter`, `graph_os.messaging.registry.MessagingRegistry`, and `graph_os.messaging.service.MessagingService` own start/stop/health/backoff/degraded detection and never persist a second durable message store. Agent-utilities owns the messaging adapters and the inbound router's message model: `agent_utilities.messaging.base.MessagingBackend` (the adapter contract each backend like telegram/mattermost implements) and `agent_utilities.messaging.models` (`EventType`, `InboundEvent`, `MessagingConfig`). This spec specifies only the serving/supervision contract and links agent-utilities' owning spec, [`AU-INTEGRATION-001`](https://github.com/Knuckles-Team/agent-utilities/tree/main/specs/au-integration-reliability) (`AU-INTEGRATION-R002`, `AU-INTEGRATION-R019`), by stable ID rather than restating it.

## Requirements

- **MSG-01 Typed supervision state machine.** A closed, typed state enum (`STOPPED`, `STARTING`, `RUNNING`, `BACKING_OFF`, `DEGRADED`, `STOPPING`) models one channel's supervised lifecycle, with a fixed table of legal transitions. A transition not in that table is refused with a typed error rather than silently applied or ignored. Delivered as the rollup of `GRAPHOS-MESSAGING-R001.1` (typed model, transition table, refusal tests) plus the router wiring that reports this state per backend.
- **MSG-02 Start is health-gated.** `InboundRouter.start()` moves a registered backend to `RUNNING` only after it observes `backend.is_connected`; an unconnected backend is never promoted to `RUNNING` and is recorded in a state the caller can distinguish from a transient startup race.
- **MSG-03 Restart backoff is bounded and resets on health.** On a recoverable listener failure, supervision moves the backend to `BACKING_OFF` and retries after an exponential delay from `MESSAGING_LISTEN_BACKOFF_BASE_S` (floor 1s) capped at `MESSAGING_LISTEN_BACKOFF_MAX_S` (default 60s); the delay never exceeds the cap and never busy-loops at zero. A run that stays healthy for at least `MESSAGING_LISTEN_HEALTHY_RESET_S` (default 60s) resets the delay to base before the next failure.
- **MSG-04 Stop drains, it does not abandon.** `InboundRouter.stop()` moves every supervised backend to `STOPPING` before cancellation, cancels listener and inbox-reaper tasks, awaits their completion (`return_exceptions=True`), and only then reports `STOPPED`; a cancellation observed during a backoff wait is treated as clean shutdown and never triggers a further restart.
- **MSG-05 Typed degraded state for a missing adapter.** When a configured channel's backend cannot be created (`MessagingRegistry.create_backend` raising `ValueError` for "not installed" or `ImportError` for missing dependencies), supervision records that channel as `DEGRADED` with the typed reason, rather than only logging a warning and silently omitting it from `create_all_enabled`'s result. A `DEGRADED` channel is visible to a status read and does not block supervision of the other configured channels.
- **MSG-06 Boundary: GraphOS supervises, agent-utilities owns adapters.** GraphOS's supervision types depend only on `agent_utilities.messaging.base.MessagingBackend`'s public adapter contract and `agent_utilities.messaging.models`; GraphOS never implements a channel adapter, and agent-utilities never implements process supervision, health gating, or backoff. `AU-INTEGRATION-R002`'s `messaging_intake_enabled` threading and `AU-INTEGRATION-R019`'s direct-reply budget are AU-owned inputs this state machine observes but does not re-implement.

## Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-MESSAGING-R001, GRAPHOS-MESSAGING-R001.1 | Typed supervision state enum, transition table, and illegal-transition refusal | BUILDING |
| GRAPHOS-MESSAGING-R002 | Health-gated start before `RUNNING` | SPECIFIED |
| GRAPHOS-MESSAGING-R003 | Bounded exponential restart backoff with healthy-run reset | SPECIFIED |
| GRAPHOS-MESSAGING-R004 | Graceful stop with task drain, no restart-after-cancel | SPECIFIED |
| GRAPHOS-MESSAGING-R005 | Typed `DEGRADED` state for a missing/uninstallable adapter | SPECIFIED |
| GRAPHOS-MESSAGING-R006 | Supervision/adapter ownership boundary against `AU-INTEGRATION-001` | SPECIFIED |

Superseded row: `GRAPHOS-HOST-R011` ([host-composition-boundary/requirements.md](../host-composition-boundary/requirements.md)) is `superseded-by` `GRAPHOS-MESSAGING-R001`–`R005` for the supervision-lifecycle detail; the row itself is kept for the broader "GraphOS serves the messaging channel and routing layer" ownership claim.

## Acceptance

1. The typed state enum and transition table exist with unit tests proving every legal transition succeeds and every illegal transition is refused with a typed error, before any router wiring is claimed complete.
2. A backend that never connects is observably not `RUNNING`; a failing listener's restart delay is bounded by the configured cap and resets after a sustained healthy run.
3. `stop()` reaches `STOPPED` only after its tasks are awaited; no task restarts after a clean cancellation.
4. A missing adapter produces a typed `DEGRADED` record, not a silent omission, and does not stop supervision of other configured channels.
5. Quality gates in [test-spec.md](test-spec.md) pass at the exact default-branch revision with no duplicate state authority.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
