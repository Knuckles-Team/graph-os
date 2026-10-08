# Implementation tasks

Check a task only after its linked code and tests land. A checked source task is not acceptance; the final release task records served evidence.

- [ ] T01 — Inventory existing fleet/MCP code and generated engine/SDK contracts; record current behavior and one owner per interface. Cover GRAPHOS-FLEET-R001, GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R004, GRAPHOS-FLEET-R006, GRAPHOS-FLEET-R007.
- [ ] T02 — Define frozen `OpSpec`, stable IDs, exact scope/effect metadata, canonical digest, reviewed engine exclusions, and generator checks. Cover GRAPHOS-FLEET-R011, GRAPHOS-FLEET-R012, GRAPHOS-FLEET-R016.
- [ ] T03 — Implement typed `invoke` with caller validation, narrowing policy, service executor, effect plans, audit, and stable errors; move child dispatch behind one fleet gateway. Cover GRAPHOS-FLEET-R015, GRAPHOS-FLEET-R021, GRAPHOS-FLEET-R003.
- [ ] T04 — Project the six intent verbs and four resident fleet tools, generated schemas/resources, cross-surface discovery, and bounded resolver. Remove obsolete harvest and granular registration in the same cutover. Cover GRAPHOS-FLEET-R013, GRAPHOS-FLEET-R014, GRAPHOS-FLEET-R005, GRAPHOS-FLEET-R006, GRAPHOS-FLEET-R007, GRAPHOS-FLEET-R020.
- [ ] T05 — Build one filtered catalog for all six item kinds, native per-session mounts, cap/expiry/eviction, queued notifications, fallback calls, and credential delegation. Cover GRAPHOS-FLEET-R018, GRAPHOS-FLEET-R001, GRAPHOS-FLEET-R004.
- [ ] T06 — Install embedded/remote Eunomia policy in graph-os; prove exact scopes and principal rules at discover/load/call and fail-closed outage behavior. Cover GRAPHOS-FLEET-R019, GRAPHOS-FLEET-R021.
- [ ] T07 — Add atomic generation refresh, durable ordered delta receipts, last-known-good fallback, in-flight drain, replay, and replica convergence. Cover GRAPHOS-FLEET-R022 and PA-12.
- [ ] T08 — Certify actual connector schemas and annotations; expose typed write-back and sanitized telemetry/security/CI feed metadata through registry ops. Cover GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R003, GRAPHOS-FLEET-R008.
- [ ] T09 — Correct generated assembly agent parsing and all-or-nothing capacity admission, one re-decision, and release on stop. Cover GRAPHOS-FLEET-R009, GRAPHOS-FLEET-R010.
- [ ] T10 — Run clean-checkout format/lint/types/tests, generated artifact and public-surface checks, CCCC, KISS, jscpd, dupehound, authority parity, and local served protocol matrix. Cover GRAPHOS-FLEET-R016, GRAPHOS-FLEET-R017.
- [ ] T11 — Land reviewed code and record merge commits per slice in `spec.md`; do not mark ACCEPTED on source evidence alone.
- [ ] T12 — Run the disposable two-replica reload and fault probe against the landed revision; attach generation, receipt, trace, timing, and policy evidence; then mark only passing slices ACCEPTED.
- [ ] T13 — Add or verify a check script that scans every fleet connector count-pin site (compatibility matrix, bundle-catalog schema, check scripts, `ontology.lock`, federated IRI, `genesis.yaml`) and fails when any site disagrees with the live connector count. Cover GRAPHOS-FLEET-R023, not covered by T01–T12.
- [ ] T14 (GRAPHOS-FLEET-R024): Take over fleet reconciliation, autoscaling, scaling authority and deploy watch from the agent runtime as typed GraphOS operations with parity and authorization tests.
- [ ] T19 (GRAPHOS-FLEET-R029): Serve the agent-utilities intent contract, register A2A, browser and RLM as `act` operations, and remove the fleet meta-tools and visibility middleware. Merge together with agent-utilities PR #54.
- [ ] T20 (GRAPHOS-FLEET-R029): Remove `MCP_TOOL_MODE` from the fleet configuration: the MCP config files, deployment manifests, config generator and README environment tables.
- [ ] T21 (GRAPHOS-FLEET-R029): Retire `MCP_ALWAYS_LOAD` and `MCP_ALWAYS_LOAD_TOOLS` and the eager-mount path in `graph_os/fleet/multiplexer.py`. A fleet call mounts what it needs.
- [ ] T22 (GRAPHOS-FLEET-R029): Rename the internal `find_tools`/`load_tools`/`unload_tools`/`multiplexer_status` methods and comments in `graph_os/fleet/` and `graph_os/api/ops/fleet.py` to the intent operation names.
- [x] T15 (GRAPHOS-FLEET-R026): Skip a registration without a component, log it, and report it in `multiplexer_status`.
- [x] T16 (GRAPHOS-FLEET-R027): Add `graph_os/fleet/onboarding.py`, the `onboard-fleet` command and the background boot pass.
- [x] T17 (GRAPHOS-FLEET-R028): Renew fleet and self-served leases each pass with windowed idempotency keys; refresh the catalog after admission.
- [ ] T18 (GRAPHOS-FLEET-R027): Bind GraphOS as the fleet importer in the deployment, roll out, and record the live `multiplexer_status` child count.
- [x] T18 (GRAPHOS-FLEET-R030): Register each connector access contract as an unapproved virtual mapping during onboarding.
- [ ] T19 (GRAPHOS-FLEET-R030): Read ontology entries from the real pack archive accessor; confirm the duck-typed `entries` read against the SDK archive.
- [ ] T20 (GRAPHOS-FLEET-R030): Bind each source to a live operation call and add an operator approval path.
