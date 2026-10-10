# Implementation tasks

Check a task only after its linked code and tests land. A checked source task is not acceptance;
the final release task records served evidence.

- [x] T01 — Define frozen `ReconciliationReceipt` and `PublicationGateResult`, and the pure
  `gate_mcp_resource_publication()` function in `graph_os/fleet/mcp_resource_reconciliation.py`.
  Cover `GRAPHOS-MCP-RESOURCES-R001.1`.
- [x] T02 — Add `tests/fleet/test_mcp_resource_reconciliation.py` covering missing, stale, digest-
  mismatched, generation-mismatched, partial-family, and wrong-tenant receipts as
  `reingestion-unreconciled`, and a fully matching fresh receipt as `reconciled`. Cover
  `GRAPHOS-MCP-RESOURCES-R001.1`.
- [ ] T03 — Confirm and link the epistemic-graph producing contract (`EG-REPO-INGEST-001`) in
  `spec.md`/`requirements.md`. Cover `GRAPHOS-MCP-RESOURCES-R002`. (Link added this PR; EG-side
  delivery is tracked in epistemic-graph's own spec, not here.)
- [ ] T04 — Wire `gate_mcp_resource_publication()` into the GRAPHOS-FLEET-001 atomic generation-
  publish step for the four MCP families. Cover `GRAPHOS-MCP-RESOURCES-R003`.
- [ ] T05 — Reconcile `docs/status.md` / `docs/fleet.md` wording against the gate's actual
  status/reason vocabulary. Cover `GRAPHOS-MCP-RESOURCES-R004`.
- [ ] T06 — Land reviewed code and record merge commits per slice in `spec.md`; do not mark
  ACCEPTED on source evidence alone.
