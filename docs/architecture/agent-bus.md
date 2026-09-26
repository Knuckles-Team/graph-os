# Agent Communication Bus (AgentBus)

> One shared graph-os hub lets **any** session — many Claude Code sessions, other LLMs,
> sessions from any first-party provider, on **any host** — register, discover each other,
> message each other, and hand work to the fleet, for the cost of the LLM calls each side
> already makes. **CONCEPT:AU-ECO.bus.agentbus-federated-agent-agent / ECO-4.85 / AU-ECO.bus.federation-relay / AU-ORCH.routing.resolve-body-single-canonical / KG-2.141 / ECO-4.87.**

## Why

The platform already had a *human*-reach core (`MessagingService`, AU-ECO.messaging.messaging-reach-service-governed) and a host-local
*invoker↔spawned-agent* channel (`agent_channel.py`, AU-ORCH.session.session-anchored-collections-native). What was missing was a way for
**independent sessions** to address and talk to **each other**. The AgentBus fills that gap by
making presence and messages first-class, durable KG objects, so the bus is cross-process,
cross-host (everyone is an HTTP client of the same engine), and survives restarts.

## Design at a glance

- **Durable-store-first.** A participant is an `:Agent` node; a message is a `:BusMessage` node
  linked to its recipient (`:hasBusMessage`); a subscription is `:Agent -[:SUBSCRIBES_TO]-> :Topic`
  (KG-2.141). No volatile in-RAM channel is on the read path, so any process on the same engine —
  including a remote session over streamable-http — sees the same roster and mailbox.
- **Cursor delivery.** `receive(since)` returns the slice after the `since` count and the new
  cursor — at-least-once, the same model as `agent_channel.receive`.
- **Presence is computed, not written.** The roster derives `online`/`offline` from `last_seen`
  vs a staleness window, so a crashed session shows offline with no reaper.
- **Governed.** Every `send` passes the fail-closed ActionPolicy `bus.send` gate; a `dispatch`
  passes `bus.dispatch` and turns a message into fleet work via `submit_loop` (AU-ORCH.routing.resolve-body-single-canonical).
- **Hybrid auth.** Cross-host participants authenticate with a JWT (the served-profile is
  fail-closed over streamable-http); local stdio stays frictionless. `agent_id` should derive
  from the authenticated `ActorContext.actor_id` so ids don't collide across hubs.
- **Two surfaces.** The `graph_bus` MCP tool and the `/graph/bus` REST twin dispatch into the one
  `AgentBus` core (ECO-4.85).

## Hub topology + mesh

<div class="admonition architecture" markdown>
<p class="admonition-title">Hub topology + mesh</p>

Within hub A (one engine), a Claude session and a GPT session both reach the
same `AgentBus` core over `graph_bus`, which reads/writes the shared
`:Agent`/`:Topic`/`:BusMessage` store and can `dispatch` into the Loop's task
lanes. Hub B (another site) is the same shape, but its remote Claude session
authenticates with a JWT. The two hubs' `AgentBus` cores connect via the
**BusFederationRelay** over A2A HTTP, forwarding and deduping by
`msg_group` — commons-only traffic crosses the hub boundary.

</div>

Within one hub, cross-host "just works": remote sessions are HTTP clients of the same engine, so
the durable mailbox is shared. Across hubs, the **BusFederationRelay** (AU-ECO.bus.federation-relay) forwards a
message group to peer hubs (registered as A2A peers carrying the `agent-bus-hub` capability),
deduping by `msg_group` and breaking loops via the `federated_from` stamp. Only `commons`-marked
traffic crosses a hub boundary (AU-KG.compute.data-is-private-its).

## Flow: send → receive → dispatch

<div class="admonition architecture" markdown>
<p class="admonition-title">Flow: send → receive → dispatch</p>

Session A calls `send(sender=A, to=B, payload)`; the bus checks the
`bus.send` ActionPolicy gate, then writes a `:BusMessage(recipient=B)` to
the KG. Session B calls `receive(B, since=cursor)`; the bus reads B's
mailbox after that cursor and returns the messages plus a new cursor.
Separately, Session A can `dispatch(objective)`; the bus checks the
`bus.dispatch` gate and hands the objective to the Loop's task lanes via
`submit_loop`.

</div>

## Store-and-forward — leave a message for a busy/offline peer (CONCEPT:AU-ECO.bus.store-and-forward-log)

A **direct** send (`to=`) already survives an offline recipient: it materializes a durable
`:BusMessage{recipient=to}` regardless of the peer's presence, so the peer picks it up on its
next `receive`. The gap was **topic** messages — a `send(topic=…)` with **zero current
subscribers** used to be dropped, and a peer that subscribed *later* never saw earlier traffic.

Store-and-forward closes both: every topic send ALSO writes one durable **topic-log** entry
(`:BusMessage{recipient="", kind="topic", expires_at}`, id `topicmsg:<group>`) on top of the
per-subscriber fan-out. A late subscriber replays that log via a **per-(agent,topic) cursor**
node (`:BusTopicCursor{agent_id,topic,last_ts}`, id `bustcur:<agent>:<topic>`) so each message
is read at most once and current subscribers (whose cursor is advanced to `now` at send time)
never get a duplicate.

- **Replay window:** by default a brand-new subscriber replays only messages **newer than its
  subscription** (no history dump). `subscribe(replay_recent=True)` backfills a bounded recent
  window (`TOPIC_REPLAY_RECENT_S`, 1h) so a joiner can catch up on what it just missed.
- **Bounded growth:** topic-log entries carry `expires_at = created + TOPIC_MSG_TTL_S` (24h); the
  bus reaper `AgentBus.prune_topic_log()` runs on the messaging daemon's existing reaper cadence
  (`router._inbox_reaper_loop`, alongside the ECO-4.83 inbox reaper) and deletes expired entries.
- **Upsert-clobber safety:** each agent's replay cursor is its **own node**, never a property on
  the shared `:Topic` node — the durable backend replaces a node's whole property blob on upsert,
  so a shared-node cursor would clobber every other agent's. (Same gotcha as `heartbeat`/inbox.)

<div class="admonition architecture" markdown>
<p class="admonition-title">Store-and-forward: a late subscriber replays the backlog once</p>

Session A sends `send(topic=news, payload)` while nobody is subscribed yet;
the bus writes a topic-log `:BusMessage(recipient="", expires_at)` and
reports `delivered:[]`, `stored:true`. Later, Session B subscribes to
`news` (seeding its `bustcur:B:news` cursor) and calls `receive(B)`; the
bus replays the topic-log after B's cursor, advances it, and returns the
backlog to B exactly once.

</div>

## Auto-register + online presence (CONCEPT:AU-ECO.bus.auto-register-online-presence)

A session that has the `graph_bus` tool **appears online to peers without an explicit
`register` call**. Every `graph_bus` action resolves an acting id (the explicit
`agent_id`/`sender`, else a stable served-session identity) and calls `AgentBus.touch(id)`,
which **auto-creates** the `:BusAgent` on first reference and **bumps `last_seen`** on every
subsequent action — so merely *using* the bus keeps you rosterable and `presence=online`
(the roster still computes staleness lazily from `last_seen`, so a vanished session goes
`offline` on its own with no reaper).

**Session identity:** on served MCP requests FastMCP injects a `Context` whose `session_id`
(fallback `client_id`) is stable for the connection's life; `bus_tools._session_identity(ctx)`
derives `session:<id>` from it so a call that passes **no** `agent_id` is still auto-registered
and presence-tracked. **Limitation:** headless/in-process calls have no `Context` (identity is
`""`), so there the caller must still pass an id explicitly — we never fabricate one. `touch`
preserves an existing agent's capability/provider blob (no upsert clobber).

## Native capability — every agent knows the bus (CONCEPT:AU-ECO.bus.agent-bus-awareness)

The bus is **not** an opt-in persona you must select; it is a native capability the *graph
shaper* (the core orchestrator) and **every spawned swarm/sub-agent** inherit, per the
*Universal capability* rule. Three seams make that true, all bottoming out at the one
`create_agent` choke point (`agent/factory.py`):

1. **Awareness in the prompt.** `bus_capability_prompt()` (`orchestration/agent_bus.py`, single source) is
   appended to every agent's system prompt when universal tools are on — so each agent knows it
   can `bus_join`/`bus_peers`/`bus_send`/`bus_check` and `dispatch`, and that it should set up
   agent-to-agent comms whenever more than one agent is involved.
2. **Actionable native tools.** `bus_join` / `bus_peers` / `bus_send` / `bus_check`
   (`tools/agent_tools.py`, registered in `tools/tool_registry.py` alongside `reach_user`) wrap
   `AgentBus` in-process, so an agent uses the bus **without** needing the graph-os MCP bound.
3. **Swarms coordinate by default.** The swarm path (`graph_orchestrate action=swarm`) stamps a
   shared topic `swarm:<hash>` into `manifest.context`, so every wave agent is told to broadcast
   progress and ask peers on that topic instead of fanning in only at synthesis.

For a deeper, focused profile there is also a standalone blueprint
`prompts/bus_coordinator.json` (+ the `mcp_config.bus.json` preset that trims graph-os to just
`graph_bus`+`graph_reach`) — used when you want a dedicated bus-first session on a small model.

<div class="admonition architecture" markdown>
<p class="admonition-title">Swarm coordination over a shared topic</p>

The orchestrator (graph shaper), which knows the bus natively, spawns a
swarm (`action=swarm`) that stamps a shared topic `swarm:abc123` into every
wave agent's manifest. Sub-agents 1–3 each carry `bus_*` tools and use
`bus_send`/`bus_check` on `swarm:abc123` to coordinate, avoid duplicating
work, and share findings with each other through the `AgentBus`. The
orchestrator separately dispatches heavy work to the Loop's task lanes.

</div>

## Surfaces & files

| Concern | Where |
|---|---|
| Core service | `agent_utilities/orchestration/agent_bus.py` (`AgentBus`) |
| Delivery/wakeup plane (AU-P1-2) | `epistemic_graph/partitioned_stream.py` (`SyncMessageDeliveryLog`) through AU `bus_log.py` tenant/envelope facade |
| MCP tool + REST twin | `agent_utilities/mcp/tools/bus_tools.py` (`graph_bus`) → `/graph/bus` |
| Native agent tools (universal) | `tools/agent_tools.py` (`bus_join`/`bus_peers`/`bus_send`/`bus_check`) + `tools/tool_registry.py` |
| Capability awareness | `bus_capability_prompt()` (`orchestration/agent_bus.py`) injected at `agent/factory.py` |
| Swarm coordination | shared `swarm_topic()` in `mcp/tools/analysis_tools.py` (`action=swarm`) |
| Standalone preset | `prompts/bus_coordinator.json` + `mcp_config.bus.json` (2-tool focused surface) |
| Federation relay | `agent_utilities/messaging/federation.py` (`BusFederationRelay`) |
| Ontology | `:BusAgent`/`:Topic`/`:BusSubscription` plus native WorkItem inbox/outbox rows |
| Store-and-forward (ECO-4.91) | EG partitioned streams with consumer cursors; the AU controller commits each recipient inbox before cursor acknowledgment |
| Auto-presence (AU-ECO.bus.auto-register-online-presence) | `AgentBus.touch()` + `bus_tools._session_identity(ctx)` (served-session id) |
| Governance | `bus.send`/`bus.dispatch` in `orchestration/action_policy.py` + `deploy/action-policy.default.yml` |
| Observability | `agent_utilities_bus_*` in `observability/gateway_metrics.py`; Grafana `agent-bus.json` |
| Load harness | `scripts/bench_bus.py` |
| Capacity model | `docs/scaling/capacity_model.py` (`bus_plan_for`) |
| Health | `system_doctor` `bus` check (`deployment/doctor.py`) |

## Delivery/wakeup plane: partitioned log, not graph fan-out (AU-P1-2)

The registry above — `:BusAgent`/`:Topic`/`:BusSubscription` — stays exactly as described: it is
small, low-churn metadata, and belongs in the KG. What does **not** belong there is the
high-volume message BODIES. Before AU-P1-2, `send()` wrote one `:BusMessage` graph node PER
RECIPIENT (fan-out — O(agents) writes) and `receive()` read the mailbox via a property-scoped
`MATCH (m:BusMessage {recipient/topic:...})` scan (O(history) reads) — a graph store pressed
into service as a queue.

`messaging/bus_log.py` is the fix: `send`/`receive` now resolve a **durable partitioned log** as
the hot delivery/wakeup plane, with real offsets/consumer cursors instead of a graph `MATCH`, a
DLQ for poison messages, and backpressure via queue depth. Three backends, resolved by
`resolve_bus_log_backend()` in preference order:

<div class="admonition architecture" markdown>
<p class="admonition-title">Delivery/wakeup plane backend resolution</p>

`AgentBus.send()` calls `resolve_bus_log_backend()`, which picks, in
order: the engine broker if reachable (`EngineBrokerBusLog`, an
AMQP-style exchange+queue per recipient); else Kafka if configured
(`KafkaBusLog`, keyed topics + a per-subscriber consumer group); else the
graph fallback (the original `:BusMessage` model, dev-only). All three
feed `AgentBus.receive()`.

</div>

1. **engine** — the epistemic-graph engine's NATIVE AMQP-style message broker (the same surface
   the `graph_broker` MCP tool exposes: `declare_exchange`/`declare_queue`/`bind`/`publish`/
   `consume`/`stats`). A `direct` exchange per tenant with ONE durable queue per recipient
   (`bus.inbox.<tenant>.<agent_id>`) gives native per-recipient delivery with no client-side
   filtering; a `fanout` exchange per (tenant, topic) with one queue per subscriber
   (`bus.subq.<tenant>.<agent_id>.<topic>`) means ONE `publish` call reaches every subscriber —
   the broker owns fan-out, never application code. Reached through the same
   `SyncEpistemicGraphClient` every `engine_<domain>` tool already uses (or directly via an
   `engine.broker` attribute — the injectable test seam).
2. **kafka** — `KafkaQueueBackend`'s keyed-partition conventions, reused: two topics
   (`agent_bus_direct`/`agent_bus_topic`), tenant-qualified partition keys
   (`bus_partition_key(tenant, target)` = `"<tenant>:<target>"`), one dedicated consumer group
   per subscriber tracking its own committed offset. The one Kafka trade-off vs the engine's
   native per-recipient queues: a subscriber's consumer is assigned every partition of the
   shared keyed topic (Kafka has no routing-key-to-queue binding), so it reads — and discards —
   traffic addressed to other recipients/tenants sharing that topic. That cost scales with total
   bus TRAFFIC, never with the number of registered agents — it is not the O(agents) fan-out
   this workstream removed.
3. **graph** (`None` from the resolver) — the ORIGINAL `:BusMessage` model, kept as the
   zero-infra dev fallback exactly as it worked before AU-P1-2. `AgentBus` runs its unchanged
   graph-node code path whenever the resolver returns `None` — never the default once a broker
   is configured.

Selection is `AGENT_BUS_LOG_BACKEND` (`engine|kafka|graph`, mirrors `TASK_QUEUE_BACKEND`): unset
= auto (engine when the engine broker is reachable — local autostart or a configured
`GRAPH_SERVICE_ENDPOINTS` remote — else Kafka when
`TASK_QUEUE_BACKEND=kafka`/`KAFKA_BOOTSTRAP_SERVERS` is set, else the graph fallback — auto mode
never attempts a real network connection with nothing configured, so zero-infra deployments and
unit tests never pay a connect-timeout cost); an EXPLICIT value is a hard contract — `engine`/
`kafka` raise `BusLogUnavailable` when unreachable, never a silent degrade.

**DLQ + backpressure:** a message that fails to decode (poison) is routed to a DLQ
queue/topic (`bus.dlq.<tenant>` / `agent_bus_dlq`) instead of blocking the consumer, and is
never handed to the caller; `read_dlq()` lets an operator inspect it. `receive()` caps the drain
at `BUS_LOG_MAX_MESSAGES_PER_RECEIVE` per call (backpressure on the hot path); `stats()` reports
queue/consumer depth for both backends.

**Dev fallback:** `ack()` and `status()["log_backend"]` both branch the same way — `ack` is a
best-effort success when log-backed (the message was already committed/acked at receive time;
there is no per-message graph node left to mark), and `status` reports which plane is active
(`"engine"` / `"kafka"` / `"graph"`).

**Known follow-up:** `replay_recent` (bounded-window backfill for a late topic subscriber) is
fully honored on Kafka (an explicit `offsets_for_times` seek) but not yet on the engine broker
(AMQP fanout only reaches queues bound at publish time — there is no time-indexed replay without
a queue already existing); federation dedup (`group_messages`/`group_exists`) still reads
`:BusMessage` nodes and is unaffected by log-backed delivery — a future workstream should move
that bookkeeping to one lightweight per-`msg_group` marker node so ECO-4.86 works identically on
every delivery plane.

## Backend note (live-validated)

Reads resolve via the epistemic-graph **engine authority** (schema-less, source of truth);
rows optionally mirror to **Postgres/pg-age**. So bus state uses a dedicated `:BusAgent` label
(not the platform's typed `:Agent` table), a `created` timestamp (the per-table `created_at`
column is reserved `TIMESTAMPTZ`), and **1-hop** property reads with subscriptions as
first-class `:BusSubscription` nodes (AGE multi-hop traversals are unreliable). These were found
and fixed via a live E2E (`reports/agent-bus-live-e2e-findings-2026-06-21.md`).

## Scale & profiling

`scripts/bench_bus.py` drives a live hub over `/graph/bus` and reports send/receive latency
percentiles + throughput, printing the modeled expectation from `docs/scaling/capacity_model.py`
alongside. The bus is durable-store-first, so its throughput is bounded by the same
single-connection engine anchor as everything else (~2 ops per delivered message). Use
`bus_plan_for(participants, msgs_per_sec, avg_recipients)` to size engine connections (shards) and
federated hubs; watch the `agent-bus` Grafana dashboard and the `bus` doctor check in production.
