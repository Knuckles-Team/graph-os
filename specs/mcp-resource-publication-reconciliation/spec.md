# MCP resource/template publication reconciliation

**Spec ID:** GRAPHOS-MCP-RESOURCES-001

**Owner:** graph-os

**State:** READY FOR IMPLEMENTATION — architecture and acceptance specified; no claim that the full surface is deployed or accepted.
**Scope IDs:** GRAPHOS-MCP-RESOURCES-R001, GRAPHOS-MCP-RESOURCES-R001.1, GRAPHOS-MCP-RESOURCES-R002, GRAPHOS-MCP-RESOURCES-R003, GRAPHOS-MCP-RESOURCES-R003.1, GRAPHOS-MCP-RESOURCES-R003.2, GRAPHOS-MCP-RESOURCES-R004.

## Outcome and state legend

graph-os is the public MCP door for the durable four-family resource/template set (tools, prompts,
resources, resource templates); epistemic-graph owns the durable graph those families are
projected from. graph-os never claims to have published a candidate generation of that set unless
epistemic-graph has handed back a receipt reconciling the exact candidate against the durable
record. Lacking that receipt, graph-os keeps returning the typed `reingestion-unreconciled` result
documented in [`docs/status.md`](../../docs/status.md) and [`docs/fleet.md`](../../docs/fleet.md)
instead of guessing or standing up a second, process-local durability store.

| State | Meaning | Required evidence |
|---|---|---|
| PROPOSED | Contract drafted, awaiting review | None |
| READY FOR IMPLEMENTATION | Reviewed design, no code claim | None |
| BUILDING | Partial source present | Branch/PR evidence per requirement |
| LANDED | Merged to default branch | Merged-head commit URL |
| ACCEPTED | Landed and release-verified | Checked-in tests + release receipt |

This is a thin contract spec: it defines the reconciliation receipt graph-os needs from
epistemic-graph before it publishes, and the typed refusal it returns until that receipt exists.
It does not define a new store, a new durability mechanism, or a replacement for the existing
fleet catalog reload design in
[`fleet-catalog-and-tools/spec.md`](../fleet-catalog-and-tools/spec.md) (GRAPHOS-FLEET-001,
requirements R022 and PA-12), which this spec narrows for the MCP resource/template publication
step specifically.

## Users and acceptance stories

1. **MCP client (P1):** calling a resource or resource-template listing method either sees the
   durable four-family set graph-os has reconciled proof for, or a typed `reingestion-unreconciled`
   refusal — never a guess at what the durable graph currently holds.
2. **Operator (P1):** `docs/status.md`'s "Explicitly unavailable surfaces" row for this capability
   stays true because the same typed result that row names is what the gate actually returns, not
   separately maintained prose.
3. **epistemic-graph contributor (P2):** the exact shape of the reconciliation receipt graph-os
   requires is public and stable, so EG-REPO-INGEST-001 work that produces it has a fixed target.

## Functional requirements

| ID | Requirement and acceptance rule |
|---|---|
| `GRAPHOS-MCP-RESOURCES-R001` | **Publication gate refuses without a valid receipt (rollup).** graph-os's MCP resource/template publication path never claims a candidate generation is published unless a `ReconciliationReceipt` exists, matches that exact candidate, and is fresh; otherwise it returns the typed `reingestion-unreconciled` result. Delivered through its child requirement, producer first. |
| `GRAPHOS-MCP-RESOURCES-R001.1` | **Typed `ReconciliationReceipt` model and publication gate (this PR).** `graph_os/fleet/mcp_resource_reconciliation.py` defines a frozen `ReconciliationReceipt` (tenant, candidate `catalog_generation`, `snapshot_digest`, acknowledged family set, issue time) and `gate_mcp_resource_publication()`, a pure function returning a typed `PublicationGateResult` whose `status` is `"reconciled"` only for a receipt matching the exact tenant, generation, digest, and all four families within a freshness bound, and `"reingestion-unreconciled"` (with a stable `reason` of `missing`, `stale`, or `invalid`) for every other case. No caller is wired to it yet (`GRAPHOS-MCP-RESOURCES-R003`, not yet specified in this slice). |
| `GRAPHOS-MCP-RESOURCES-R002` | **epistemic-graph is the sole admissible receipt source.** The only receipt the gate may treat as reconciling is one epistemic-graph issues once the durable graph has actually committed the candidate's four-family projection; graph-os does not synthesize, cache past expiry, or otherwise locally fabricate a passing receipt. The producing contract is owned by epistemic-graph as [`EG-REPO-INGEST-001`](https://github.com/Knuckles-Team/epistemic-graph/blob/main/specs/repository-index-and-ingestion/spec.md) (`specs/repository-index-and-ingestion` in that repository); this spec consumes that ID and does not redefine it. |
| `GRAPHOS-MCP-RESOURCES-R003` | **Gate wired at the one catalog-publish entry point (rollup).** The fleet catalog's atomic generation-publish step (`fleet-catalog-and-tools` R022/PA-12) calls `gate_mcp_resource_publication()` before swapping the active generation pointer for the four MCP families; only an all-families `"reconciled"` result allows the swap. Split because the publish step it must call is not yet built in graph-os (`fleet-catalog-and-tools` R022/PA-12 is still BUILDING, so there is no entry point to wire). Delivered through its children. |
| `GRAPHOS-MCP-RESOURCES-R003.1` | **Swap guard function.** `graph_os/fleet/mcp_resource_reconciliation.py` adds `require_reconciled_for_swap()`, which wraps `gate_mcp_resource_publication()` and raises a typed `PublicationRefusedError` carrying the `PublicationGateResult` for any `reingestion-unreconciled` result, and returns the receipt only for `reconciled`. The publish step can then call one function that cannot be bypassed by ignoring a return value. Not built in this slice. |
| `GRAPHOS-MCP-RESOURCES-R003.2` | **Call the guard from the publish step (depends on `fleet-catalog-and-tools` R022/PA-12 landing in graph-os).** The atomic generation-publish step calls `require_reconciled_for_swap()` before swapping the active generation pointer for the four families, leaves the last-known-good generation active on refusal, and surfaces the typed result in the reload outcome. Not built in this slice. |
| `GRAPHOS-MCP-RESOURCES-R004` | **Status docs are the gate's result, not hand-maintained prose.** `docs/status.md`'s "Explicitly unavailable surfaces" row and `docs/fleet.md`'s reconciliation paragraph describe exactly the `reingestion-unreconciled` result this gate returns; a change to the gate's status/reason vocabulary requires updating both in the same change. Not built in this slice. |

## Reuse and non-goals

Reuse `graph_os/fleet/catalog_reader.py`'s existing `FleetCatalogIntegrityError` / receipt-mismatch
pattern as prior art for a typed, fail-closed receipt check; this spec does not replace that reader
or its `ReadReceipt`, which cover live `:Server`/component reads, not four-family MCP publication.
This spec introduces no new durable store: the receipt is read from epistemic-graph, never written
or cached as a second source of truth inside graph-os.
