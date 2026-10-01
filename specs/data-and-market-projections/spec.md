# GRAPHOS-DATA-MARKET — Schema context, source admission, and finance projection

## Status legend

`DRAFT` needs design decisions; `READY TO BUILD` has a complete contract;
`BUILDING` has an active implementation; `LANDED` means code merged to this
repository's default branch; `ACCEPTED` means the tests and served behavior in
`test-spec.md` passed against the merged revision. The delivery and acceptance
states are independent. Evidence must name a revision and the exact command or
served observation. This spec claims no implementation or acceptance evidence.

## Outcome and scope

A caller can inspect a relational schema, see source ownership and change impact,
admit one application at a time to a shared data plane, and use governed finance
read, schedule, and alert surfaces without bypassing GraphOS's tenant and policy
boundary. GraphOS owns authenticated serving, fleet composition, dispatch,
subscriptions, scheduling integration, and source admission workflow. The graph
engine owns catalog facts, ontology mappings, query reasoning, durable market
records, and finance calculations. Connector services own external account and
market transport. Agent workflows may explain results but cannot authorize an
order or invent evidence. Browser UI is a separate presentation client.

The public contract must work for a contributor with their own local PostgreSQL
or MariaDB test fixture, a local graph engine, and a mock market provider. It
must not assume a particular private network, inventory, source application,
broker, or notification channel.

## User stories and functional requirements

### Schema context (GRAPHOS-DATA-MARKET-R005)

- **FR-01:** Authenticated REST and MCP callers can ask `schema_context` with
  `source_id`, `schema`, `object`, and `intent` (`definition`, `joins`, `ontology`,
  `owner`, or `impact`). A response includes stable object IDs, catalog revision,
  typed relationships, evidence for inferred joins, mapping approval state,
  cited provenance, and `as_of` time. `impact` includes dependent views, mappings,
  and consumers with bounded traversal. Undeclared or unverified relationships
  are labelled as proposals, never as approved facts.
- **FR-02:** GraphOS authorizes the tenant and source before requesting any
  schema context. The request is bounded (`depth`, `limit`, timeout, response
  bytes); invalid scope returns a typed client error and inaccessible objects
  have the same nonrevealing response as nonexistent objects.
- **FR-03:** If the graph engine has not published a schema-context capability
  or the source has no approved catalog snapshot, GraphOS returns typed
  `UNAVAILABLE` with no fabricated empty graph or ontology match.

### Application admission (GRAPHOS-DATA-MARKET-R004)

- **FR-04:** An authorized operator can dry-run admission of a named application
  and source into a shared data plane. The plan lists source dialect and version,
  read-only credentials scope, catalog and mapping readiness, query conformance,
  connector overlap, tenant boundaries, expected cutover behavior, and rollback
  steps. Dry run causes no source mutation or connector retirement.
- **FR-05:** Admission is one application at a time with an idempotency key,
  persisted state machine, explicit operator approval, health and parity probes,
  and a rollback fence. A connector is retired only after the admitted source is
  healthy, parity checks pass, and an operator approves the retirement. Failed
  admission retains the old read path and reports exact recovery action.
- **FR-06:** An application cannot be admitted if source grants are broader than
  the tenant, its mapping is unapproved, conformance is missing, or rollback is
  unproven. No migration of application-owned tables is implied by admission.

### Finance projection and scheduling (GRAPHOS-DATA-MARKET-R001 / GRAPHOS-DATA-MARKET-R002 / GRAPHOS-DATA-MARKET-R003 / GRAPHOS-DATA-MARKET-R006)

- **FR-07:** GraphOS exposes tenant-scoped market reads through one application
  service shared by REST and MCP. Each price, trend, portfolio, and positions
  field includes source identity, as-of time, currency or unit, and staleness;
  bounded server-side pagination and decimation occur after authoritative
  calculations. Reads require `finance:read`. Missing tenant/account binding
  fails closed before touching a process-global provider account.
- **FR-08:** Backfill, scan, DCA-due, and flip-explainer jobs use the existing
  scheduler and agent workflow contract. A schedule records tenant, owner,
  authorized action, timezone, trigger, idempotency key, and last run. DST and
  replay cannot produce duplicate effects. A model explanation cites the exact
  calculation inputs and is informational only.
- **FR-09:** GraphOS subscribes durably to the finance event topic family,
  dispatches through configured notification channels, and deduplicates by
  tenant, event ID, and subscriber. Redelivery after restart has at-least-once
  transport with idempotent user-visible delivery. An alert never authorizes
  an order, changes a portfolio, or advances an approval lease.
- **FR-10:** Paper order submission, if exposed, requires a tenant-bound account,
  explicit `finance:paper-trade` grant, durable lease and idempotency receipt.
  Unverified account binding returns typed `UNAVAILABLE` before any provider
  call. Live order execution belongs to the connector's governed write-back
  contract; GraphOS can submit only a separately approved, tenant-bound request
  through that published contract.
- **FR-11:** A recurring DCA plan stores its owner, tenant, symbol or asset ID,
  account binding, amount and currency as decimal values, IANA timezone,
  recurrence rule, start/end bounds, pause state, and paper/live mode. Each due
  time produces a durable proposal with a deterministic key derived from plan
  revision and scheduled occurrence. Editing a plan creates a new revision;
  replay or clock drift cannot create a second proposal for the same occurrence.
  Paper mode may execute only under the paper grant and lease in FR-10. Live
  mode remains proposal-only until a human approves the exact preview and a
  connector write-back lease is consumed. Changing the amount, account,
  symbol, mode, policy or quote after approval invalidates that lease. A paused
  or revoked plan emits no order request. Every proposal and effect links the
  source quote, as-of time, account, approval and reconciliation receipt.

## Acceptance criteria

1. REST and MCP responses for each owned read/action are identical after
   normalization, and both use the same authorization and application service.
2. Two-tenant tests prove a request for tenant A cannot read B's schema, market
   account, schedule, alert, or admission state; denied requests cause zero
   downstream calls.
3. Restart and replay tests prove admission state and alert dedupe persist;
   failed admission rolls back without retiring the old connector.
4. Tests prove unsupported engine capabilities and missing account binding
   return typed errors before any external effect.
5. Fresh-checkout tests and the repository's release gate pass without requiring
   private infrastructure. A served local fixture demonstrates one catalog
   answer, one admission rollback, one finance read, and one duplicate alert.

## Traceability

| Requirement | Design | Tests | Evidence |
|---|---|---|---|
| FR-01–03 | `plan.md` schema context | SC-01–05 | PENDING |
| FR-04–06 | `plan.md` admission | AD-01–06 | PENDING |
| FR-07–11 | `plan.md` finance | FI-01–10 | PENDING |

## Open decisions

- The graph engine's published schema-context method name and response schema
  must be pinned before implementation; GraphOS may not infer it from an
  internal module or silently fall back to raw catalog reads.
- The durable event and schedule APIs must be pinned to a published contract;
  delivery guarantees are at-least-once plus idempotency, not an unsupported
  exactly-once claim.
- The account binding authority for paper trading must be documented and
  verified before that operation can change from `UNAVAILABLE` to enabled.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
