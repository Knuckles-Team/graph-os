# Hosted API and intent operations — tasks

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. See [design](plan.md) and [tests](test-spec.md).

- [ ] Validate public engine package contracts and exact method/error/scope coverage; identify one existing GraphOS entrypoint per domain.
- [ ] Implement immutable operation registry, digest, bindings, exclusions, and deterministic generation.
- [ ] Implement shared invocation steps, safe error mapping, durable plan lease, audit reservation/outcome, policy and service executor.
- [ ] Implement six MCP intent verbs, typed discovery/ranking, ten resident-tool boot invariant, and multi-kind fleet loading through `invoke`.
- [ ] Implement `/api/v1` operation and resource route factories, protocol-route inventory, generated OpenAPI and Python/TypeScript clients.
- [ ] Build the checked former-tool/route parity inventory, migrate domain handlers and generated-client consumers, then delete obsolete GraphOS registrars/routes and duplicate authority logic in one serving cutover.
- [ ] Prove contract, authorization, policy, idempotency, audit, negative and served cases in [test-spec.md](test-spec.md).
- [ ] Run pinned CCCC, KISS, jscpd, Dupehound, Python, package and CI gates from a clean public checkout; capture exact revision evidence.
- [ ] Review public documentation and mark only individually proven capabilities accepted.
- [x] **GRAPHOS-OPS-R021.1:** Decide operations (service-executed) — decide slice of `GRAPHOS-OPS-R021`.
- [ ] **GRAPHOS-OPS-R021.2:** Retrieval, context, and freshness read operations — retrieval slice of `GRAPHOS-OPS-R021`.
- [ ] **GRAPHOS-OPS-R021.3:** Policy and swarm read operations — policy slice of `GRAPHOS-OPS-R021`.
- [ ] **GRAPHOS-OPS-R022.1:** Work-item and offer operations — work slice of `GRAPHOS-OPS-R022`.
- [ ] **GRAPHOS-OPS-R022.2:** Evolution loop, schedule, and proposal operations — evolution slice of `GRAPHOS-OPS-R022`.
- [ ] **GRAPHOS-OPS-R024.1:** Telemetry operations — telemetry slice of `GRAPHOS-OPS-R024`.
- [ ] **GRAPHOS-OPS-R024.2:** Security operations — security slice of `GRAPHOS-OPS-R024`.
- [ ] **GRAPHOS-OPS-R024.3:** Usage operations — usage slice of `GRAPHOS-OPS-R024`.
- [ ] **GRAPHOS-OPS-R024.4:** Memory operations — memory slice of `GRAPHOS-OPS-R024`.
- [ ] **GRAPHOS-OPS-R024.5:** Operational admin operations — admin slice of `GRAPHOS-OPS-R024` (graph/query/search/ontology excluded; owned by `GRAPHOS-HOST-R026.x`).
- [ ] **GRAPHOS-OPS-R026.1:** API-surface drift gate — api-surface slice of `GRAPHOS-OPS-R026`.
- [ ] **GRAPHOS-OPS-R026.2:** Backward-compatibility drift gate — backward-compat slice of `GRAPHOS-OPS-R026`.

## Decomposition children (tracked)

- [x] **GRAPHOS-OPS-R020:** Ingest operations through a typed SDK runner facade
- [x] **GRAPHOS-OPS-R020.1:** Typed ingest runner port and the source-sync entry point
- [x] **GRAPHOS-OPS-R020.2:** Repository indexing and source inventory operations
- [x] **GRAPHOS-OPS-R020.3:** Pack and job management operations
- [ ] **GRAPHOS-OPS-R020.4:** Drift listing and repair-with-approval operations
- [ ] **GRAPHOS-OPS-R020.5:** Embedding admission and re-embedding operations

## Decomposition children (tracked)

- [ ] **GRAPHOS-OPS-R017:** Finance and markets operations with tenant isolation
- [ ] **GRAPHOS-OPS-R017.1:** Remaining scope of GRAPHOS-OPS-R017 (slice .1): Finance and markets operations with tenant isolation
- [ ] **GRAPHOS-OPS-R017.2:** Remaining scope of GRAPHOS-OPS-R017 (slice .2): Finance and markets operations with tenant isolation
- [ ] **GRAPHOS-OPS-R018:** Query, search, ontology, and analytics operations
- [ ] **GRAPHOS-OPS-R018.1:** Remaining scope of GRAPHOS-OPS-R018 (slice .1): Query, search, ontology, and analytics operations
- [ ] **GRAPHOS-OPS-R018.2:** Remaining scope of GRAPHOS-OPS-R018 (slice .2): Query, search, ontology, and analytics operations
- [ ] **GRAPHOS-OPS-R019:** Federation source registration and sharing operations
- [ ] **GRAPHOS-OPS-R019.1:** Remaining scope of GRAPHOS-OPS-R019 (slice .1): Federation source registration and sharing operations
- [ ] **GRAPHOS-OPS-R019.2:** Remaining scope of GRAPHOS-OPS-R019 (slice .2): Federation source registration and sharing operations
- [x] **GRAPHOS-OPS-R020:** Ingest operations through a typed SDK runner facade
- [x] **GRAPHOS-OPS-R020.1:** Typed ingest runner port and the source-sync entry point
- [x] **GRAPHOS-OPS-R020.2:** Repository indexing and source inventory operations
- [x] **GRAPHOS-OPS-R020.3:** Pack and job management operations
- [ ] **GRAPHOS-OPS-R020.3.1:** Remaining scope of GRAPHOS-OPS-R020.3 (slice .1): Pack and job management operations
- [ ] **GRAPHOS-OPS-R020.3.1.1:** `ingest.packs.list` op
- [ ] **GRAPHOS-OPS-R020.3.1.2:** `ingest.jobs.status` op
- [ ] **GRAPHOS-OPS-R020.4:** Drift listing and repair-with-approval operations
- [ ] **GRAPHOS-OPS-R020.5:** Embedding admission and re-embedding operations
- [ ] **GRAPHOS-OPS-R020.3.1:** Remaining scope of GRAPHOS-OPS-R020.3 (slice .1): Pack and job management operations
- [ ] **GRAPHOS-OPS-R022.2.1:** `evolution.loops.status` op
- [ ] **GRAPHOS-OPS-R022.2.2:** `evolution.schedules.list` op
- [ ] **GRAPHOS-OPS-R022.2.3:** `evolution.proposals.list` op
- [ ] **GRAPHOS-OPS-R022.2.4:** `evolution.loops.run` op
- [ ] **GRAPHOS-OPS-R022.2.5:** `evolution.loops.pause` op
- [ ] **GRAPHOS-OPS-R031:** Published tool-to-operation parity inventory
- [ ] **GRAPHOS-OPS-R031.1:** Remaining scope of GRAPHOS-OPS-R031 (slice .1): Published tool-to-operation parity inventory
- [ ] **GRAPHOS-OPS-R032:** Identity dependency inversion via ports
- [ ] **GRAPHOS-OPS-R032.1:** Remaining scope of GRAPHOS-OPS-R032 (slice .1): Identity dependency inversion via ports
- [ ] **GRAPHOS-OPS-R032.2:** Remaining scope of GRAPHOS-OPS-R032 (slice .2): Identity dependency inversion via ports
- [ ] **GRAPHOS-OPS-R032.2.1:** Remaining scope of GRAPHOS-OPS-R032.2: Identity runtime constructor in the composition root.
- [ ] **GRAPHOS-OPS-R032.2.2:** Remaining scope of GRAPHOS-OPS-R032.2: Inject identity ports into mcp_server.
- [ ] **GRAPHOS-OPS-R032.2.3:** Remaining scope of GRAPHOS-OPS-R032.2: Inject identity ports into webui_host and webui_co_service.
- [ ] **GRAPHOS-OPS-R032.2.3.1:** Remaining scope of GRAPHOS-OPS-R032.2.3: compose_web_application requires injected identity ports.
- [ ] **GRAPHOS-OPS-R032.2.3.2:** Remaining scope of GRAPHOS-OPS-R032.2.3: Thread identity ports through run_web_ui and the webui_host package.
- [ ] **GRAPHOS-OPS-R032.2.4:** Remaining scope of GRAPHOS-OPS-R032.2: Integration test of single injected identity runtime.
- [ ] **GRAPHOS-OPS-R035:** Multiplexer registration reduced to four resident tools
- [ ] **GRAPHOS-OPS-R035.1:** Remaining scope of GRAPHOS-OPS-R035 (slice .1): Multiplexer registration reduced to four resident tools
- [ ] **GRAPHOS-OPS-R035.2:** Remaining scope of GRAPHOS-OPS-R035 (slice .2): Multiplexer registration reduced to four resident tools
