# Delivery tasks and evidence

Status vocabulary is defined in [spec.md](spec.md). Check a task only after its listed proof exists; source presence alone is not a check mark.

- [x] Extract and publish the GraphOS serving, gateway, fleet, A2A and deployment package boundary. Evidence: package modules under `graph_os/`, public `graph-os` console script in `pyproject.toml`. **State: Implemented; release/served acceptance for this whole spec still pending.**
- [ ] Inventory all dependency-internal imports and duplicate host scripts; attach a machine-readable owner/port map and the initial forbidden-import count.
- [ ] Add missing public engine, SDK and AU ports in their owner packages and release/install them.
- [ ] Replace private imports throughout GraphOS with public ports and enforce a zero-import architecture test.
- [ ] Route MCP, REST, A2A, messaging and UI requests through one typed application service and one authorization decision.
- [ ] Reconcile duplicate gateway/deployment/MCP/fleet/A2A behavior and remove old host entrypoints after parity tests.
- [ ] Prove one event-loop owner and bounded lifecycle under simultaneous co-services, reload and cancellation.
- [ ] Run clean-checkout contract/full/quality gates and a wheel install smoke; link exact commit, CI run and wheel hash.
- [ ] Run served authorization and receipt parity against an isolated release environment; attach redacted evidence and mark Accepted only after this passes.
- [ ] GRAPHOS-HOST-R019: Mount `/api/dashboard/*` and `/ws/dashboard` from `graph_os.gateway.dashboard_api`. Evidence: `tests/gateway/test_dashboard_api.py`. **State: Building.** Remaining: served check of `/api/dashboard/full` (200) and `/ws/dashboard` (snapshot frame) after rollout.
- [ ] GRAPHOS-HOST-R008 (slice 1 of N): move `security/security_policy_middleware.py` into `graph_os/security/`. Evidence: PR #66 (open).
- [ ] GRAPHOS-HOST-R008 (slice 2 of N): move `ecosystem/agent_manager_dashboard.py` into `graph_os/ecosystem/`. Evidence: PR #68 (open).
- [ ] GRAPHOS-HOST-R008 (slice 3 of N): move `observability/gateway_health.py` into `graph_os/observability/`. Evidence: `graph_os/observability/gateway_health.py`, `tests/observability/test_gateway_health_evidence_live_path.py` (5 of the AU file's 5 tests ported; the 5th AU test, which proved `agent_utilities.observability.gateway_metrics`' middleware calls this module, stays in the agent runtime since that module has not moved). The AU copy's only in-repo importer is a function-local, try/except-wrapped, explicitly best-effort call inside `gateway_metrics.py` — deleting the AU copy exercises that module's own documented fallback path rather than breaking it; a companion `agent-utilities` PR adds a regression test proving exactly that. **State: Building** (PR open). Remaining (per `agent_utilities/architecture/component-registry-boundary-seeds.yml` AU-BOUNDARY-R014): `security/request_identity.py`, `security/error_surface.py`, `security/browser_auth.py`, `security/auth.py`, `security/conformance/**`, `observability/gateway_metrics.py`, `observability/runtime_health.py`, `observability/health.py`, `observability/health_ingest.py`, and the remaining `core`/`knowledge_graph` modules in that seed group — one module per slice, ascending by in-repo importer count, re-verified by bare-name grep each time (a dotted-path grep misses relative imports).

**Current evidence gap:** No exact commit and served receipt set is recorded here for the strict public-port cutover or the complete parity matrix. Preserve `Implemented in part / acceptance pending` until those are attached.
