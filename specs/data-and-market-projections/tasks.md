# GRAPHOS-DATA-MARKET — implementation tasks

Each task has an observable done condition. Dependencies are ordered so that
GraphOS does not claim a capability before its published downstream contract
exists. Current status for every task: **WAITING**.

- [ ] **GDM-01:** Pin graph-engine schema-context and finance API versions and the connector account-binding contract. Done when public client schemas and compatibility errors are recorded and SC-04/FI-02 fixtures compile.
- [ ] **GDM-02:** Add strict schema-context request/response models and one application service. Done when SC-01–04 pass and the service consumes the public graph-engine client with provenance.
  - [x] Models (`graph_os/gateway/schemas/schema_context.py`) and the
    capability-gated service (`graph_os/gateway/schema_context_service.py`)
    land SC-01 (strict rejection) and SC-04 (typed `UNAVAILABLE`, zero
    engine dispatch) for GRAPHOS-DATA-MARKET-R005.
  - [ ] SC-02/SC-03 wait on epistemic-graph publishing a `schema_context`
    client; no such capability exists yet to dispatch against.
- [ ] **GDM-03:** Wire MCP and REST schema-context routes through that service. Done when SC-05 served parity proves identical authorization and bounded errors.
- [ ] **GDM-04:** Add a durable application-admission aggregate and dry run with source, mapping, conformance and rollback checks. Done when AD-01/03/06 pass and dry run has no effects.
- [ ] **GDM-05:** Implement approved attach, verify, activate, reconciliation and rollback, with connector retirement as a separate authorized step. Done when AD-02/04/05 pass on local PostgreSQL and MariaDB fixtures.
- [ ] **GDM-06:** Add a tenant-scoped finance application service and read models, resolving account binding before dispatch. Done when FI-01–03/09 pass, including two-tenant zero-delegation denial.
- [ ] **GDM-07:** Bind backfill, scan, DCA-due and explanation actions to the existing scheduler/workflow contract. Done when FI-04/05 pass across DST and replay cases.
- [ ] **GDM-07a:** Persist recurring DCA plans and one proposal per plan revision/occurrence; gate paper and live dispatch on distinct grants, exact preview and connector write-back lease. Done when FI-10 passes across replay, pause, revocation, approval invalidation and uncertain external outcome.
- [ ] **GDM-08:** Add durable finance outbox subscription, delivery dedupe, channel routing and restart reconciliation. Done when FI-06/07 pass and alerts have no order route.
- [ ] **GDM-09:** Gate paper submit on account binding, grant, lease and idempotency; retain typed unavailability until all contracts are served. Done when FI-08 passes with zero effects on every failed prerequisite.
- [ ] **GDM-10:** Run full test, type, lint, scanner and served fixture acceptance; update public status and this spec's evidence. Done when exact merged revision and results support `LANDED` and `ACCEPTED` independently.

Before proposing a PR, compare changed modules against existing implementations
for CCCC complexity, jscpd syntax duplication, dupehound semantic duplication,
and KISS simplicity. Follow the repository's normal gate; do not substitute
private live services for local fixtures or silently downgrade a failing test.
