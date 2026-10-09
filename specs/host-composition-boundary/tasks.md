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
- [ ] GRAPHOS-HOST-R008 (slice 1 of N): move `security/security_policy_middleware.py` from the agent runtime into `graph_os/security/`. Evidence: `graph_os/security/security_policy_middleware.py`, `tests/security/test_security_policy_middleware.py` (10 cases). The AU copy had zero in-repo importers and zero tests, so nothing there needed rewiring; its deletion is tracked in a separate `agent-utilities` PR. **State: Building.** Remaining (per `agent_utilities/architecture/component-registry-boundary-seeds.yml` AU-BOUNDARY-R014): `security/request_identity.py`, `security/error_surface.py`, `security/browser_auth.py`, `security/auth.py`, `security/conformance/**`, the `observability` health/metrics modules, and the `core`/`ecosystem`/`knowledge_graph` modules in that seed group — one module per slice.

**Current evidence gap:** No exact commit and served receipt set is recorded here for the strict public-port cutover or the complete parity matrix. Preserve `Implemented in part / acceptance pending` until those are attached.
