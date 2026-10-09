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
