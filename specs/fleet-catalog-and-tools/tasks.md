# Implementation tasks

Check a task only after its linked code and tests land. A checked source task is not acceptance; the final release task records served evidence.

- [x] T01 — Inventory existing fleet/MCP code and generated engine/SDK contracts; record current behavior and one owner per interface. Cover GRAPHOS-FLEET-R001, GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R004, GRAPHOS-FLEET-R006, GRAPHOS-FLEET-R007.
- [x] T02 — Define frozen `OpSpec`, stable IDs, exact scope/effect metadata, canonical digest, reviewed engine exclusions, and generator checks. Cover GRAPHOS-FLEET-R011, GRAPHOS-FLEET-R012, GRAPHOS-FLEET-R016.
- [x] T03 — Implement typed `invoke` with caller validation, narrowing policy, service executor, effect plans, audit, and stable errors; move child dispatch behind one fleet gateway. Cover GRAPHOS-FLEET-R015, GRAPHOS-FLEET-R021, GRAPHOS-FLEET-R003.
- [x] T04 — Project the six intent verbs and four resident fleet tools, generated schemas/resources, cross-surface discovery, and bounded resolver. Remove obsolete harvest and granular registration in the same cutover. Cover GRAPHOS-FLEET-R013, GRAPHOS-FLEET-R014, GRAPHOS-FLEET-R005, GRAPHOS-FLEET-R006, GRAPHOS-FLEET-R007, GRAPHOS-FLEET-R020. GRAPHOS-FLEET-R006 is now a rollup over GRAPHOS-FLEET-R006.1-.5; GRAPHOS-FLEET-R007 is now a rollup over GRAPHOS-FLEET-R007.1-.3.
  - [x] GRAPHOS-FLEET-R005.1 (child, producer): `graph_os/a2a/work_items.py` (`WorkItemView`, `work_item_view`, `parse_work_item_view`, `list_work_item_views`) + `tests/a2a/test_work_items.py`.
  - [ ] GRAPHOS-FLEET-R006.1 (child, producer): `tests/fleet/test_harvest_inventory.py` pins the current harvest call-site baseline in `graph_os/fleet/multiplexer.py`.
  - [ ] GRAPHOS-FLEET-R006.2 (child, consumer of .1): cut `_probe_skills`/`_probe_prompts` over to the resident `build_local_skill_catalog` path for an already-admitted child, keeping fallback for a non-admitted one.
  - [ ] GRAPHOS-FLEET-R006.3 (child, consumer of .2): delete the now-dead `_harvest_resource_bodies`, `_harvest_deadline`, `_harvest_error_reason`, `_SKILL_HARVEST_SPEC`, `_PROMPT_HARVEST_SPEC` from `graph_os/fleet/multiplexer.py`.
  - [ ] GRAPHOS-FLEET-R006.4 (child, consumer of .3): rename `_harvest_probe_results` to `_settled_probe_results` in `graph_os/fleet/multiplexer.py`.
  - [ ] GRAPHOS-FLEET-R006.5 (child, closing audit, consumer of .1-.4): `tests/fleet/test_no_harvest_remains.py` asserts the R006.1 baseline is now zero.
  - [ ] GRAPHOS-FLEET-R007.1 (child, producer, cross-repo: agent-connector-sdk): `agent_connector_sdk/mcp/server.py`'s `create_mcp_server()` registers a native `.prompt()`/`.resource()`/`.resource_template()`.
  - [ ] GRAPHOS-FLEET-R007.2 (child, consumer of .1, cross-repo: agent-utilities): `agent_utilities/mcp/graphos_surface.py`'s `register_graphos_surface()` backing `FastMCP("graph-os-backing")` registers native prompts/resources/templates.
  - [ ] GRAPHOS-FLEET-R007.3 (child, consumer of .2, this repo): `graph_os/mcp_server/server.py` serves the composed loop end to end + `tests/mcp/test_fastmcp4_native.py`.
- [x] T05 — Build one filtered catalog for all six item kinds, native per-session mounts, cap/expiry/eviction, queued notifications, fallback calls, and credential delegation. Cover GRAPHOS-FLEET-R018, GRAPHOS-FLEET-R001, GRAPHOS-FLEET-R004.
- [x] T06 — Install embedded/remote Eunomia policy in graph-os; prove exact scopes and principal rules at discover/load/call and fail-closed outage behavior. Cover GRAPHOS-FLEET-R019, GRAPHOS-FLEET-R021.
- [ ] T07 — Add atomic generation refresh, durable ordered delta receipts, last-known-good fallback, in-flight drain, replay, and replica convergence. Cover GRAPHOS-FLEET-R022 and PA-12.
- [x] T08 — Certify actual connector schemas and annotations; expose typed write-back and sanitized telemetry/security/CI feed metadata through registry ops. Cover GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R003, GRAPHOS-FLEET-R008.
- [ ] T08 — Certify actual connector schemas and annotations; expose typed write-back and sanitized telemetry/security/CI feed metadata through registry ops. Cover GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R003, GRAPHOS-FLEET-R008.
- [ ] T09 — Correct generated assembly agent parsing. Cover GRAPHOS-FLEET-R010. (GRAPHOS-FLEET-R009 split into T09.1-T09.3 below.)
- [x] T09.1 (GRAPHOS-FLEET-R009.1): Build the typed capacity-acquisition primitive (ledger): all-or-nothing acquire, exactly one re-decision on denial, release on stop.
- [ ] T09.2 (GRAPHOS-FLEET-R009.2): Wire the capacity ledger into `control_plane/runs/admission.py`'s `admit_once`.
- [ ] T09.3 (GRAPHOS-FLEET-R009.3): Wire the capacity ledger into `graph_os/a2a/routing.py` and `composition.py`'s publish step.
- [x] T08 — Certify actual connector schemas and annotations; expose typed write-back and sanitized telemetry/security/CI feed metadata through registry ops. Cover GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R003, GRAPHOS-FLEET-R008. GRAPHOS-FLEET-R003 is now a rollup over GRAPHOS-FLEET-R003.1-.3.
  - [x] GRAPHOS-FLEET-R002.1 (child, producer): `graph_os/fleet/catalog_items.py` (`compute_schema_fingerprint`, `CatalogItem.schema_fingerprint`, wired into `_probed_item`) + `tests/fleet/test_schema_fingerprint.py`.
  - [x] GRAPHOS-FLEET-R003.1 (child, producer): `graph_os/api/ops/write_back.py` (`WriteBackPreviewRequest`, `WriteBackPreviewResult`, `preview_write_back`) + `tests/api/test_write_back_preview.py`.
  - [ ] GRAPHOS-FLEET-R003.2 (child): bind the GRAPHOS-FLEET-R003.1 preview model into the operation registry as an `OpSpec` (`Confirm.PLAN`, `Idempotency.KEY_REQUIRED`), plus a restart/fault test.
  - [x] GRAPHOS-FLEET-R008.1 (child, producer): `graph_os/fleet/feeds.py` (`FeedEvent`, `FeedEventRejected`, `rum_event`, `security_audit_event`, `cicd_event`) + `tests/fleet/test_feeds.py`.
  - [ ] GRAPHOS-FLEET-R008.2 (child): wire real connector ingestion for each feed and pass events through `connector_items()`/`_pack_annotations()` into the registry/tenant-boundary check.
- [ ] T09 — Correct generated assembly agent parsing and all-or-nothing capacity admission, one re-decision, and release on stop. Cover GRAPHOS-FLEET-R009, GRAPHOS-FLEET-R010.
  - [ ] GRAPHOS-FLEET-R009.1 (child): `graph_os/a2a/routing.py` evaluates a candidate run against the current admission state.
  - [ ] GRAPHOS-FLEET-R009.2 (child): `graph_os/a2a/composition.py` commits an evaluated run together with its synthesis evidence.
  - [ ] GRAPHOS-FLEET-R009.3 (child): `control_plane/runs/admission.py` publishes a committed run only after an all-or-nothing capacity acquisition, with one re-decision on denial and release on stop.
  - [ ] GRAPHOS-FLEET-R003.1 (child, producer): `graph_os/api/ops/write_back.py` (`WriteBackPreviewParams`, `WriteBackCommitParams`, `WriteBackPreview`, `WriteBackReceipt`, `derive_write_back_idempotency_key`) + `tests/api/test_write_back_models.py`.
  - [ ] GRAPHOS-FLEET-R003.2 (child, consumer of .1): register `fleet.write_back.preview`/`fleet.write_back.commit` as typed `OpSpec` entries in `graph_os/api/ops/write_back.py`, wired into `graph_os/api/ops/registry_factory.py` + `tests/api/test_write_back_ops.py`.
  - [ ] GRAPHOS-FLEET-R003.3 (child, consumer of .2): restart/fault test `tests/fleet/test_write_back_restart.py` proving idempotent replay and preview gating survive a service restart.
- [ ] T09 — Correct generated assembly agent parsing and all-or-nothing capacity admission, one re-decision, and release on stop. Cover GRAPHOS-FLEET-R009, GRAPHOS-FLEET-R010.
- [x] T09.1 — Capacity-acquisition primitive (acquire/release, one re-decision) in `graph_os/control_plane/runs/admission.py`. Cover GRAPHOS-FLEET-R009.1.
- [ ] T09.2 — Wire `graph_os/a2a/routing.py`/`composition.py` evaluate/commit/publish through the GRAPHOS-FLEET-R009.1 primitive. Cover GRAPHOS-FLEET-R009.2.
- [ ] T10 — Run clean-checkout format/lint/types/tests, generated artifact and public-surface checks, CCCC, KISS, jscpd, dupehound, authority parity, and local served protocol matrix. Cover GRAPHOS-FLEET-R016, GRAPHOS-FLEET-R017.
- [ ] T11 — Land reviewed code and record merge commits per slice in `spec.md`; do not mark ACCEPTED on source evidence alone.
- [ ] T12 — Run the disposable two-replica reload and fault probe against the landed revision; attach generation, receipt, trace, timing, and policy evidence; then mark only passing slices ACCEPTED.
- [ ] T13 — Add or verify a check script that scans every fleet connector count-pin site (compatibility matrix, bundle-catalog schema, check scripts, `ontology.lock`, federated IRI, `genesis.yaml`) and fails when any site disagrees with the live connector count. Cover GRAPHOS-FLEET-R023, not covered by T01–T12.
- [x] T14 (GRAPHOS-FLEET-R024): Take over fleet reconciliation, autoscaling, scaling authority and deploy watch from the agent runtime as typed GraphOS operations with parity and authorization tests. Split into T14.1–T14.3 below; GRAPHOS-FLEET-R024 is now a rollup over GRAPHOS-FLEET-R024.1–R024.3.
  - [x] GRAPHOS-FLEET-R023.1 (child, producer): `graph_os/fleet/connector_count_pins.py` + `scripts/check_connector_count_pins.py` + `tests/test_check_connector_count_pins.py`. Real sites (compatibility matrix, bundle-catalog schema, `ontology.lock`, federated IRI) remain cross-repo/AU-owned follow-up.
- [ ] T14 (GRAPHOS-FLEET-R024): Take over fleet reconciliation, autoscaling, scaling authority and deploy watch from the agent runtime as typed GraphOS operations with parity and authorization tests.
- [ ] T14 (GRAPHOS-FLEET-R024): Take over fleet reconciliation, autoscaling, scaling authority and deploy watch from the agent runtime as typed GraphOS operations with parity and authorization tests. Split into T14.1–T14.3 below; GRAPHOS-FLEET-R024 is now a rollup over GRAPHOS-FLEET-R024.1–R024.3.
- [x] T14.1 (GRAPHOS-FLEET-R024.1): Port the deploy-watch verdict decision (`agent_utilities.orchestration.deploy_watch.run_deploy_watch`'s probe-reduction rule) as a typed `fleet.deploy_watch.evaluate` GraphOS operation behind the shared registry caller/scope/policy chokepoint, with parity tests against the original success/failed/unobserved rule and a negative test refusing a caller without the `mcp:delegate` scope.
- [ ] T14.2 (GRAPHOS-FLEET-R024.2): Port the remaining fleet reconciler, autoscaler, scaling-authority and deploy-watch scheduling/rollback-dispatch behavior as typed GraphOS operations, wired into the served operation registry, with parity and authorization tests matching T14.1's pattern.
- [ ] T14.3 (GRAPHOS-FLEET-R024.3): Cross-repo — remove the superseded reconciler/autoscaler/scaling-authority/deploy-watch modules from agent-utilities' `orchestration/` package and redirect its agent runner and planning code to call the GraphOS operations from T14.1–T14.2. Blocked on T14.2 landing; out of scope for this (graph-os-only) lane.
- [x] T19 (GRAPHOS-FLEET-R029): Serve the agent-utilities intent contract, register A2A, browser and RLM as `act` operations, and remove the fleet meta-tools and visibility middleware. Merge together with agent-utilities PR #54. (Landed via PR #51, merge commit `db8d44259c3da06c2b0a48b685ca87a01debaaca`; `graph_os/mcp_server/server.py` calls `backing_server(mcp)` from `agent_utilities.mcp.graphos_surface`; no `SessionVisibilityMiddleware` class remains; `tests/mcp_server/test_graph_tool_surface.py` passes.)
- [x] T20 (GRAPHOS-FLEET-R029): Remove `MCP_TOOL_MODE` from the fleet configuration: the MCP config files, deployment manifests, config generator and README environment tables.
- [x] T21 (GRAPHOS-FLEET-R029): Retire `MCP_ALWAYS_LOAD` and `MCP_ALWAYS_LOAD_TOOLS` and the eager-mount path in `graph_os/fleet/multiplexer.py`. A fleet call mounts what it needs.
- [x] T22 (GRAPHOS-FLEET-R029): Rename the internal `find_tools`/`load_tools`/`unload_tools`/`multiplexer_status` methods and comments in `graph_os/fleet/` and `graph_os/api/ops/fleet.py` to the intent operation names.
- [x] T15 (GRAPHOS-FLEET-R026): Skip a registration without a component, log it, and report it in `multiplexer_status`.
- [x] T16 (GRAPHOS-FLEET-R027): Add `graph_os/fleet/onboarding.py`, the `onboard-fleet` command and the background boot pass.
- [x] T17 (GRAPHOS-FLEET-R028): Renew fleet and self-served leases each pass with windowed idempotency keys; refresh the catalog after admission.
- [x] T18 (GRAPHOS-FLEET-R027): Bind GraphOS as the fleet importer in the deployment, roll out, and record the live `multiplexer_status` child count.
- [x] T24 (GRAPHOS-FLEET-R027): Skip re-attest and re-import for an already-admitted server whose captured pack digest is unchanged; renew it instead of re-onboarding. Re-attesting unchanged content reused attest's digest-derived idempotency key under a freshly randomized request body, which EG refused as `IDEMPOTENCY_CONFLICT` on every pass after the first.
- [x] T18 (GRAPHOS-FLEET-R030): Register each connector access contract as an unapproved virtual mapping during onboarding.
- [x] T19 (GRAPHOS-FLEET-R030): Read ontology entries from the real pack archive accessor; confirm the duck-typed `entries` read against the SDK archive.
- [x] T23 (GRAPHOS-FLEET-R030): Parse only ontology bodies that use the access-contract vocabulary. Log and skip a body the parser rejects.
- [x] T20 (GRAPHOS-FLEET-R030): Bind each source to a live operation call and add an operator approval path.
- [x] T24 (GRAPHOS-FLEET-R031): Add `MCPMultiplexer._catalog_tool_probe_info` reading tool descriptors off the installed EG catalog snapshot, and fall back to it in `discover_tools` only for a server whose live probe errored.

## MCPProjection reachability follow-up (2026-10-08, this lane)

Investigated whether `graph_os/api/mcp/verbs.py`'s `MCPProjection` (R013) should
get a real caller outside `graph_os/mcp_server/server.py`'s boot sequence, per
R012/R013's spec text. Finding: no, not as written. `agent_utilities.mcp.graphos_surface`
— the module PR #51 actually wired into the served bridge (`backing_server(mcp)`
in `server.py`) — imports only `agent_utilities.mcp._graphos_action_manifest`,
`intent_contract` and `tool_specs`. It does not import anything from
`graph_os.api.*`. Confirmed with `grep -n "^from\|^import"` on
`agent_utilities/mcp/graphos_surface.py`.

That means the whole `graph_os/api/registry/`, `graph_os/api/invoke/` and
`graph_os/api/mcp/` tree (R011, R012, R013, R014, R015, plus R007's
`graph_os/api/mcp/resources.py` native `@mcp.resource(...)` registrations) has
no production caller and no architectural reason to gain one: the six-verb
surface it was built to serve is already shipped through a separate,
self-contained implementation. Wiring `MCPProjection` into any serving path
now would stand up a second, competing tool surface next to the one actually
in production — not a fix, a new problem. Recommend the orchestrator choose
one of:
- delete `graph_os/api/registry/`, `graph_os/api/invoke/`, `graph_os/api/mcp/`
  and their tests (confirm no other reachable caller first), or
- mark R011/R012/R013/R014/R015 and R007's resource-registration clause
  `SUPERSEDED` by R029, the same disposition already due for R018/R020.

Also checked GRAPHOS-FLEET-R002's "empty placeholder" tool-schema fingerprint
language. The one hit, `policy.fingerprint_catalog(catalog)`
(`graph_os/fleet/multiplexer.py:2918`), calls a method that
`_RUNTIME_CHILD_POLICY_METHODS` (same file, line 1093) requires any runtime
child policy to implement, but this is a *catalog*-level fingerprint on the
transport/spawn policy protocol, not the per-tool-pin schema fingerprint
R002 describes. Did not find where a per-tool-pin schema fingerprint is
computed or defaulted to empty; R002's concrete gap still needs locating
before it can be fixed.

## Spec-sweep audit notes (2026-10-08, this lane)

- GRAPHOS-FLEET-R026/R027/R028/R029/R030 are confirmed `LANDED` on `origin/main` as of `7e3628a`: `graph_os/fleet/onboarding.py` matches their descriptions line for line, `tests/fleet/test_fleet_onboarding.py` + `tests/fleet/test_fleet_access_contracts.py` + `tests/test_fleet_catalog_reader.py` + `tests/mcp_server/test_graph_tool_surface.py` all pass (33 tests) through the shared venv against this worktree's checkout. Nothing further to do here.
- GRAPHOS-FLEET-R029's own spec text says it "supersedes the four resident tools in GRAPHOS-FLEET-R018 and GRAPHOS-FLEET-R020." Now that R029 is landed (PR #51 deleted the `SessionVisibilityMiddleware` class and the five fleet meta-tools), R018 and R020 should be marked superseded, not left `BUILDING`/`SPECIFIED` with their old "replace the live multiplexer's own registered meta-tools" language — that replacement already happened via a different mechanism (`agent_utilities.mcp.graphos_surface`), not via R018's own `graph_os/fleet/catalog_composition.py`/`multiplexer_ops.py` module, which still has no non-test caller.
- Flag for re-audit: `status.json` currently marks GRAPHOS-FLEET-R012/R013/R015 `LANDED`, but `MCPProjection` (`graph_os/api/mcp/verbs.py`) is constructed only in `tests/api/test_mcp_verbs.py` — grep for a non-test importer finds none. The six-verb surface that actually ships is the separate `agent_utilities.mcp.graphos_surface` path from PR #51, not this `graph_os/api/mcp/` package. Per the "landed means reachable" rule, this looks like a promotion based on merged-commit evidence alone, not reachability; the orchestrator should re-check before trusting these three as done, or confirm `graph_os/api/mcp/` is deliberately superseded like R018/R020.
- Remaining `BUILDING` items (R003, R011, R016, R017, R022) each need either composition into the served app's boot sequence (`graph_os/mcp_server/server.py`'s bootstrap block, explicitly out of scope for this lane) or a real design reconciliation, not a cherry-pick: their recorded branch commits (`9a9b3f5c8e`, `3f5f354a36`, `fb06321fe1`, `1f3b30ff69`) all conflict against current `main`, and R022's conflict is a genuine behavioral regression — the branch commit raises `FleetCatalogIntegrityError` on an unadmitted server, while main's current (landed, R026) behavior is to skip and warn so boot never fails. Porting it as-is would reintroduce a boot-failure mode R026 just removed. None of these were touched this lane.

Checked the remaining `SPECIFIED` requirements (R004, R005, R006, R008, R009)
for existing partial code to extend instead of a fresh guess: none has one.
R004's capability/digest/modality/cost/latency fields do not exist anywhere
in `graph_os/fleet/catalog_items.py`; R006's skill/prompt harvest removal
would cut deep into `graph_os/fleet/multiplexer.py`'s probe-budget and
security-sensitive harvest machinery (not a small deletion); R009's capacity
admission has no code under `graph_os/control_plane/runs/` or `graph_os/a2a/`
beyond an unrelated audit-chain capacity check. None of these is a safe
10-minute slice without a real design decision first; none was attempted.

## Decomposition children (tracked)

- [ ] **GRAPHOS-FLEET-R002.2:** Remaining scope of GRAPHOS-FLEET-R002 (slice .2): Tool schema fingerprints recomputed from served schemas (rollup)
- [ ] **GRAPHOS-FLEET-R005.2:** Remaining scope of GRAPHOS-FLEET-R005 (slice .2): A2A task routing with typed work-item and lease operations (rollup)
- [ ] **GRAPHOS-FLEET-R008:** RUM, security-audit and CI/CD feeds join the catalog (rollup)

## Decomposition children (tracked)

- [ ] **PA-12:** Catalog reload rejects invalid candidates, proves replica convergence
- [ ] **PA-12.1:** Remaining scope of PA-12 (slice .1): Catalog reload rejects invalid candidates, proves replica convergence
- [ ] **PA-12.2:** Remaining scope of PA-12 (slice .2): Catalog reload rejects invalid candidates, proves replica convergence
- [ ] **PA-12.1:** Remaining scope of PA-12 (slice .1): Catalog reload rejects invalid candidates, proves replica convergence (rollup)
- [ ] **PA-12.2:** Remaining scope of PA-12 (slice .2): Catalog reload rejects invalid candidates, proves replica convergence (rollup)
