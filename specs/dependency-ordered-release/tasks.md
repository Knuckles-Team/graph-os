# GRAPHOS-RELEASE-001 — Implementation tasks

Status: SPECIFIED. Governing [spec](spec.md) and [plan](plan.md).

- [ ] Inventory existing release profile, doctor certification, canary, production operations and self-deploy interfaces; agree on candidate schema with pipeline and repository-manager owners.
- [ ] Add one validated candidate manifest reader and deterministic dependency graph to the existing deployment path; reject untrusted, mutable, cyclic and incompatible candidates.
- [ ] Connect digest-pinned profile apply to stage readiness and meaningful functional probes; record exact revision and digest at each stage.
- [ ] Implement stop, retry and rollback decisions with observed post-rollback checks; expose partial recovery honestly.
- [ ] Run every T-RL positive/negative fixture in portable CI and release-only hosted qualification at the exact candidate digest.
- [ ] Publish public receipt and operator instructions; change status only when landed and acceptance evidence independently exists.
