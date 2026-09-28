# GRAPHOS-DATA-MARKET — implementation tasks

Each task has an observable done condition. Dependencies are ordered so that
GraphOS does not claim a capability before its published downstream contract
exists. Current status for every task: **WAITING**.

| Task | Work | Done condition |
|---|---|---|
| GDM-01 | Pin graph-engine schema-context and finance API versions and connector account-binding contract. | Public client schemas and compatibility errors recorded; SC-04 and FI-02 fixtures compile. |
| GDM-02 | Add strict schema-context request/response models and one application service. | SC-01–04 pass; service consumes public graph-engine client and preserves provenance. |
| GDM-03 | Wire MCP and REST schema-context routes through that service. | SC-05 served parity passes with identical authorization and bounded errors. |
| GDM-04 | Add durable application-admission aggregate and dry-run with source, mapping, conformance and rollback checks. | AD-01/03/06 pass; dry-run has no effects. |
| GDM-05 | Implement approved attach, verify, activate, reconciliation and rollback; keep connector retirement as a separate authorized step. | AD-02/04/05 pass on local PostgreSQL and MariaDB fixtures. |
| GDM-06 | Add tenant-scoped finance application service and read models; resolve account binding before dispatch. | FI-01–03/09 pass, including two-tenant zero-delegation denial. |
| GDM-07 | Bind backfill, scan, DCA-due and explanation actions to the existing scheduler/workflow contract. | FI-04/05 pass across DST and replay cases. |
| GDM-07a | Persist recurring DCA plans and one proposal per plan revision/occurrence; gate paper and live dispatch on their distinct grants, exact preview and connector write-back lease. | FI-10 passes across replay, pause, revocation, approval invalidation and uncertain external outcome. |
| GDM-08 | Add durable finance outbox subscription, delivery dedupe, channel routing and restart reconciliation. | FI-06/07 pass; alerts have no order route. |
| GDM-09 | Gate paper submit on account binding, grant, lease and idempotency; retain typed unavailability until all contracts are served. | FI-08 passes with zero effects on every failed prerequisite. |
| GDM-10 | Run full test, type, lint, scanner and served fixture acceptance; update public status and this spec's evidence. | Exact merged revision and results support `LANDED` and `ACCEPTED` independently. |

Before proposing a PR, compare changed modules against existing implementations
for CCCC complexity, jscpd syntax duplication, dupehound semantic duplication,
and KISS simplicity. Follow the repository's normal gate; do not substitute
private live services for local fixtures or silently downgrade a failing test.
