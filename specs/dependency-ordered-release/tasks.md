# GRAPHOS-RELEASE-001 — Implementation tasks

Status: SPECIFIED. Governing [spec](spec.md) and [plan](plan.md).

- [x] Inventory existing release profile, doctor certification, canary, production operations and self-deploy interfaces; agree on candidate schema with pipeline and repository-manager owners. `graph_os.deployment.release_candidate` reuses `genesis_environments.load_environment_profile` for the profile binding rather than adding a second parser.
- [x] Add one validated candidate manifest reader and deterministic dependency graph to the existing deployment path; reject untrusted, mutable, cyclic and incompatible candidates. Landed as `graph_os.deployment.release_candidate` (`Candidate`/`Artifact`/`Stage`/`Profile` typed models, `read_candidate`, `_dependency_graph`, `_order`) with `tests/deployment/test_release_candidate.py` (32 cases): deterministic topological order with a stable component-ID tie-break (`test_deterministic_optional_order`), cycle refusal (`test_cycle_and_compatibility`, `candidate_cycle`), missing/disabled-predecessor refusal (`test_missing_or_disabled_predecessor`), and closed-schema/duplicate/digest-tag/signature refusals. No rollout execution and no rollback yet — `plan_candidate` only plans (`executed: False`); see the next two tasks.
- [ ] Connect digest-pinned profile apply to stage readiness and meaningful functional probes; record exact revision and digest at each stage.
- [ ] Implement stop, retry and rollback decisions with observed post-rollback checks; expose partial recovery honestly.
- [ ] Run every T-RL positive/negative fixture in portable CI and release-only hosted qualification at the exact candidate digest.
- [ ] Publish public receipt and operator instructions; change status only when landed and acceptance evidence independently exists.

## GRAPHOS-RELEASE-R003 (split 2026-10-09: net-new, 7 readiness-obligation code roots)

- [ ] `GRAPHOS-RELEASE-R003.1` — typed `ExitCriterionRow`/`ExitCriteriaMatrix` model plus `read_exit_criteria_matrix` validation, with refusal tests for a duplicate obligation ID, an empty matrix, and a malformed test-ID reference.
- [ ] `GRAPHOS-RELEASE-R003.2` — the one entry point that loads a real matrix (committed fixture or CLI command) and the ingestion-receipt row's mapped test.
- [ ] `GRAPHOS-RELEASE-R003.3` — SPARQL and natural-language query-proof rows.
- [ ] `GRAPHOS-RELEASE-R003.4` — multi-agent orchestration-graph row.
- [ ] `GRAPHOS-RELEASE-R003.5` — connector-certification row.
- [ ] `GRAPHOS-RELEASE-R003.6` — write-back receipt row.
- [ ] `GRAPHOS-RELEASE-R003.7` — browser-and-identity-provider login probe row.
- [ ] `GRAPHOS-RELEASE-R003.8` — typed-abstention response row.
- [ ] A test-suite audit confirms every exit-criteria row resolves to a passing automated test before `GRAPHOS-RELEASE-R003` itself is marked LANDED.
## Decomposition children (tracked)

- [ ] **GRAPHOS-RELEASE-R003:** Exit-criteria matrix maps readiness to tests
- [ ] **GRAPHOS-RELEASE-R003.1:** Remaining scope of GRAPHOS-RELEASE-R003 (slice .1): Exit-criteria matrix maps readiness to tests
- [ ] **GRAPHOS-RELEASE-R003.2:** Remaining scope of GRAPHOS-RELEASE-R003 (slice .2): Exit-criteria matrix maps readiness to tests
