# Verification contract

Every assertion is run against an exact commit and reports a pass/fail result. Fixture tests are
required for a clean checkout and cloud PR; served release probes are separately recorded before
ACCEPTED. Do not turn an unavailable external environment into a blocking source hook.

## Positive and negative cases

| Area | Positive case | Negative case and required outcome |
|---|---|---|
| Receipt model | A fully matching, fresh receipt (exact tenant, generation, digest, all four families) | Missing receipt returns `reingestion-unreconciled`/`missing` |
| Freshness | Receipt issued within `max_age_ms` of now | Receipt older than `max_age_ms` returns `reingestion-unreconciled`/`stale` |
| Candidate match | Receipt's tenant, `catalog_generation`, and `snapshot_digest` match the candidate exactly | Any one mismatched field returns `reingestion-unreconciled`/`invalid` |
| Family coverage | Receipt acknowledges exactly `{tools, prompts, resources, resource_templates}` | A receipt acknowledging a subset of the four families returns `reingestion-unreconciled`/`invalid` |
| Publish wiring (R003, not built in this slice) | A `reconciled` result allows the generation swap | A `reingestion-unreconciled` result blocks the swap and the result is surfaced, not swallowed |
| Docs consistency (R004, not built in this slice) | `docs/status.md` / `docs/fleet.md` name the same status/reason vocabulary as the gate | A vocabulary change in the gate without a matching docs update fails review |

## This slice's test file

`tests/fleet/test_mcp_resource_reconciliation.py` covers: missing receipt, stale receipt, digest
mismatch, generation mismatch, partial family acknowledgement, wrong tenant (all
`reingestion-unreconciled`), and one fully matching fresh receipt (`reconciled`).
