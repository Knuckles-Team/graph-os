# GRAPHOS-DATA-MARKET — architecture and implementation design

## Ownership and reuse inventory

GraphOS is the composition root. Keep one live request path:

`REST or MCP -> GraphOS authentication/policy -> application service -> published
graph-engine or fleet contract -> typed response and audit`.

Reuse `graph_os.mcp_server.runtime` endpoint factories and `_run_json_endpoint`
for REST/MCP parity. The existing `graph_analyze_code_context_endpoint` and
`_code_context_kwargs` show the bounded context route pattern; reuse the pattern,
not its `graph_code` tool name or code-specific payload. Reuse
`graph_os.gateway.schemas.graph_core` Pydantic request discipline
(`extra="forbid"`), `graph_os.fleet.multiplexer` for admitted fleet calls,
`graph_os.gateway.fleet` for the fleet surface, and
`graph_os.control_plane.runs.admission` for run admission conventions. GraphOS
must not implement catalog extraction, ontology mapping, finance math, market
provider transport, or a second agent scheduler.

The current checkout has a served code-context route and generic graph tool
dispatch. It does **not** establish that schema context, finance ops, or durable
finance subscriptions are implemented. All three remain pending until code and
served tests provide evidence.

## Schema context

Define a strict request model in `graph_os.gateway.schemas.graph_core` (or a
small adjacent schema module) with `source_id`, `schema`, `object`, `intent`,
`depth` 0–3, `limit` 1–100, optional catalog revision. Define a versioned
response with source and tenant ID, object ID, catalog revision, `as_of`, facts,
proposals, dependency edges, citations, and `truncated`. Limits are enforced
before dispatch and again on returned payload. Add one application service,
then wire both `POST /graph/schema/context` and a `graph_schema` MCP action to
it. Both entrypoints must use identical identity, policy, and error mapping.

The service asks the graph engine through its published client API. Required
engine behavior: a deterministic catalog graph, declared and inferred joins
with evidence, approved ontology mapping state, and bounded dependency
traversal. GraphOS can only project these fields, never derive an unsupported
match. Capability discovery occurs at startup or first request. A missing or
incompatible version yields `UNAVAILABLE`; malformed input yields
`INVALID_ARGUMENT`; denied or hidden objects yield `NOT_FOUND` without leaking
existence; engine timeout yields retryable `UNAVAILABLE`. Structured errors
carry a correlation ID and no secret source DSN.

## Application admission

Introduce an `ApplicationAdmission` aggregate in the existing control-plane
repository style with stable `(tenant_id, application_id, source_id)` identity
and states `DRAFT`, `VALIDATED`, `APPROVED`, `ATTACHING`, `VERIFYING`, `ACTIVE`,
`ROLLING_BACK`, `ROLLED_BACK`, and `FAILED`. Persist the idempotency key and
transition receipt before external actions. An operator can call dry-run,
approve, attach, verify, activate, and rollback via one service with REST/MCP
twins. The service calls the graph-engine catalog/mapping and query conformance
contracts, and the existing fleet control contract for connector state. The
source's credentials remain in the source service or secret provider; GraphOS
stores only opaque references.

Dry-run returns a signed or hash-addressed plan snapshot. Approval pins that
snapshot. Attach creates a named read-only source projection, then verifies
catalog revision, tenant scope, representative queries, drift monitor, and
rollback handle. Only an explicit separate retirement decision after `ACTIVE`
may disable the legacy ingest connector. Rollback restores routing first,
checks parity and health, then detaches projection. Any unknown effect leaves
the admission in a reconcilable state rather than claiming successful rollback.
Use local PostgreSQL and MariaDB fixtures; application identity is configurable
and examples use generic `sample_app` tables.

## Finance read, schedule, and event bridge

Build one finance application service exposed by REST and MCP. GraphOS receives
an authenticated tenant and resolves an explicit account binding before any
positions or paper-order call. It requests authoritative calculations and
records from the graph engine through a published client contract, and market
transport from an admitted connector service through the fleet. It never
imports provider clients into gateway widgets or accepts a process-global
account as proof of tenant ownership. Responses retain decimal values as
strings, source, as-of, session, currency/unit, freshness, and provenance;
avoid float money. Bound market query range, row count, and timeout before
dispatch. Server-side decimation applies to finalized series only.

Use the existing scheduler integration and workflow executor rather than a
second timer. Every job carries a tenant-scoped action token, schedule ID,
trigger ID, lease, and deterministic run key. Job types: historical backfill,
market scan, DCA due, and explanation. Backfill is read/idempotent; any action
that could create an order requires a separate write authorization. Timezone
handling uses IANA names and explicit DST policy. Explanation output must cite
the exact calculation/record IDs and label uncertainty; it cannot emit an
authorization token.

Subscribe through the durable finance outbox topic family. Project each event
to a delivery record keyed by `(tenant, subscriber, event_id, channel)` with a
durable acknowledgement and retry policy. A restart reads the cursor and
reconciles unacknowledged deliveries. Route to a configured channel adapter;
the adapter receives only the minimum data permitted by its subscriber policy.
An alert dispatch has no route to order submission. Paper trading remains
disabled until tenant-to-provider-account binding and graph-engine grant/lease
contracts are verified end to end.

For a recurring DCA plan, ask the existing scheduler for each due occurrence
and persist a proposal before any execution. The proposal key is
`(tenant, plan_id, plan_revision, scheduled_at_utc)`; an occurrence has one
proposal across restarts, timezone transitions and competing workers. Read
the current quote and account binding into a versioned preview. Paper mode
routes through the paper operation only after its grant and lease checks.
Live mode routes only through the connector SDK's governed write-back path
after a human approves the exact preview and the approval lease is consumed.
Approval and connector effect receipts are joined to the proposal, and an
unknown external effect enters reconciliation rather than being retried as a
new order. Pause, revocation, policy change or changed preview data cancels
the pending dispatch without claiming to reverse an earlier effect.

## Cross repository contract and dependency order

1. The graph engine publishes versioned schema-context, catalog revision,
   mapping status, finance record, and event APIs. GraphOS consumes public
   clients only. Its catalog/profiling and finance math are separate owners.
2. The connector SDK and connector services publish source identity, health,
   market transport, and account binding; GraphOS consumes an admitted fleet
   tool. No provider-specific import is allowed in GraphOS.
3. Agent workflows publish a scheduler and explanation interface; GraphOS
   handles authentication and dispatch while those workflows own agent logic.
4. Browser clients consume GraphOS read and operation APIs, with no privileged
   direct graph-engine or provider path.

For local development, use mock interfaces implementing these versioned
contracts. A mock proves GraphOS wiring, while final acceptance additionally
requires a served graph-engine fixture and a local database fixture. A missing
dependency is a typed `UNAVAILABLE`, never a compatibility alias.

## Quality and maintainability

Keep each behavior in one owning service and route both transports through it.
Run CCCC against changed Python modules, jscpd for duplicated code, dupehound
for semantic duplicates, KISS review for unnecessary abstraction, Ruff, mypy,
pytest, and the repository's release workflow. Any scanner finding requires a
real fix or a documented existing baseline comparison; do not suppress it to
manufacture a green gate. Record exact commands, revisions, and results in the
spec evidence before marking `ACCEPTED`.
