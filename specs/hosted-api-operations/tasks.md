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
