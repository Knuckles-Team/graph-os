# Architecture and implementation plan

## Existing components

`graph_os.deployment.genesis_environments` already models ten closed profile sections and reference-only secrets. `config_generator` and `cli` provide configuration commands; `preflight` checks profile-specific host prerequisites; `doctor`, `doctor_coverage` and `doctor_certification` diagnose installed capabilities; `release_canary` checks installed entrypoints and engine binary without starting services; `production_ops` performs guarded operational actions. `deploy/environments/` contains sample profile data. Extend these owners and their tests instead of adding a second installer, configuration parser or doctor.

The package defines `graph-os`, `graph-os-daemon`, `setup-config`, `agent-utilities-doctor`, `agent-utilities-venv`, `graph-os-release-canary` and `graph-os-production-ops` scripts. Script existence is not functional proof; clean wheel installation and representative invocations are required.

## Configuration contract

The parser accepts one explicit profile with sections `environment`, `target`, `release`, `runtime`, `filesystem`, `configuration`, `secrets`, `network`, `identity`, `validation`. Each section has a closed schema; unknown and missing fields are errors. `secrets` contains only resolvable references such as environment, vault or Kubernetes secret references. Runtime path bindings are anchored to declared writable mounts. The resolved profile yields a deterministic digest, used in rendered resource annotations and validation receipts. A difference between rendered and applied digest is a refusal.

The environment adapter resolves the profile and validates host capabilities, then renders a reviewable plan with create/update/delete inventory. It must not read a secret value during a dry run. Apply requires explicit target confirmation and a validated identity mode. Post-apply probes use the same public GraphOS API and observe readiness, authorization, engine connection and fleet catalog; they do not reconstruct success from the existence of a file.

## Profiles and dependency boundaries

| Profile | Required inputs | Guarantees |
|---|---|---|
| Local development | Python, `uv`, published graph engine and agent packages; loopback binding | Unit/contract checks run without network services; optional disposable served checks isolate state under a temporary directory. |
| Single-host container | Container runtime, pinned images, explicit writable volumes and secret references | One GraphOS host, one graph authority, guarded identity and repeatable startup/recovery. |
| Kubernetes | Namespace/RBAC target, pinned image digest, secret-provider references, mounts and probes | Explicit service account, rollout/rollback, readiness and post-deploy functional receipts. |

The renderer can vary by target, but profile validation, config resolution, engine contract verification and functional check definitions are shared. Keep engine storage and graph semantics in `epistemic-graph`; GraphOS owns only deployment composition and diagnostics. The WebUI is an optional co-service, not a required core import. Public sample profiles use placeholder domains and secret references only.

## Delivery order

1. Freeze the public profile schema and add a sample development profile whose requirements a contributor can meet from a fresh checkout.
2. Complete the GraphOS-owned deployment port migration and command parity; delete agent-runtime duplicate entrypoints after an installed-wheel comparison.
3. Make Compose and Kubernetes renderers consume the same validated profile object; require digest-pinned images and sealed secret references.
4. Add disposable first-boot fixtures and CI service orchestration. Split required portable PR checks from optional production integration acceptance.
5. Document the exact commands on GitHub Pages and link this contract from the contribution guide; publish a verified package and run the external acceptance tier.

## GRAPHOS-DEPLOY-R016 plan

The serving container stays at the client role. A second container in the same pod runs the host daemon. It reuses the serving image, source mounts and process identity configuration. It reaches the engine over the shared Unix socket volume. A non-empty endpoint setting makes the engine resolver connect-only. The daemon therefore never starts a second engine. The daemon shares the task-queue data volume, so work that clients enqueue reaches the host.

## GRAPHOS-DEPLOY-R017 plan: engine-routed deployment placement and assembly

The agent-utilities planner (deleted by commit `db29b2da6`, AU-BOUNDARY-R002.2) is not re-created. Graph-os holds no plan type, no backend-selection logic and no renderer.

### Why this is not a separate planner

The engine decision-engine spec makes `graph.assemble()` the sole authority for composing an agent graph from typed tasks and capabilities (`EG-DECISION-ENGINE-R036`), routes tasks through the decision ladder (`EG-DECISION-ENGINE-R044`), and exposes topology and placement selection as one typed Decide question (`EG-DECISION-ENGINE-R046`, `R111`). A graph-os planner would be a second path to the same decisions. Graph-os only maps a request to the engine, presents the answer and gets human confirmation.

### Design

1. **Placement.** One typed Decide request/response mapping (R017.1). Engine prerequisite: a deployment-placement question kind under `EG-DECISION-ENGINE-R046`/`R111` (ID to be assigned by the engine spec; not invented here).
2. **Sequencing.** Ordered fleet-tool steps are an `AgentAssemble` graph (R017.2). Present it read-only (R017.3), confirm through `graphos.plan/confirm` (R017.4, rows `GRAPHOS-A2A-R005.1`/`R006.1`, with `decide.commit`). Typed refusal while unavailable (R017.5). Engine prerequisite: the `AgentAssemble` handler in `graph_os/a2a/routing.py` and `admission.py` is a refusal stub and must be replaced; the confirm surface is also a fail-closed stub.
3. **Artifacts.** Existing files only (R017.6): `deploy/compose/compose.yaml`, `deploy/helm/graph-os`, `deploy/swarm/stack.yml`, selected by `deploy/environments/{dev,test,prod}.yaml`. No systemd artifact exists in `deploy/`; systemd is explicitly unsupported (decision D5 below).
4. **In-process.** Unchanged `start_composed_services`, composition from `graph_os.mcp_server.composition` (R017.7).
5. **Command.** `setup-config deploy` is a thin subcommand that asks the engine and prints the decision and graph (R017.8).

### Parity table

| Old entry point | Now | Status |
|---|---|---|
| `DeploymentPlan`, `DeploymentStep`, `FleetToolCall`, `rendered_summary` | engine assembly (`AgentAssemble`, `EG-DECISION-ENGINE-R036`) | dropped as parallel |
| `DeploymentBackend`, `get_backend`, `PlanOnlyBackendError` | engine decision (placement Decide, `R046`/`R111`) | dropped as parallel |
| `detect_composition` / `CompositionPlan` | `graph_os.mcp_server.composition` | exists |
| `InProcessBackend` | `start_composed_services` | exists (R017.7 verifies) |
| `render_compose`, `ContainerBackend` | `deploy/compose` | existing artifact |
| `render_k8s_manifest`, `KubernetesBackend` | `deploy/helm/graph-os` | existing artifact |
| Swarm (never covered by old planner) | `deploy/swarm/stack.yml` | existing artifact |
| `render_systemd_unit`, `NativeShellBackend` | none | not supported (no artifact in `deploy/`) |
| `deploy-plan` CLI | `setup-config deploy` asking the engine | to build (R017.8) |
| Floating `latest` image default | none | dropped (digest-pinned only) |

### Out of scope

Executing any plan-only fleet call; container-manager, tunnel-manager and systems-manager tool contracts (owned by those repositories); replacing `deploy/helm` or the profile renderers required by GRAPHOS-DEPLOY-R003/R013; Swarm planning (the old planner never covered it).

## R017 caveat resolutions (design, 2026-10-10)

### Problem and evidence

`scripts/check_deployment_image_digests.py` walks Kubernetes workload kinds (`_POD_PATHS`) in rendered YAML and accepts only `repository@sha256:<64 hex>`; its docstring states that tags and unrendered templates are unsupported. A Compose or Swarm file has no `kind`, so `_check_resource` raises "kind: expected a nonempty string" on it. R017.6 and `tests/deployment/test_deploy_artifacts.py` therefore cannot run the one checker over all three artifacts today. The only tests of the script are `tests/deployment/test_image_digests.py` (Kubernetes fixtures); `tests/deployment/test_graphos_deploy_artifacts.py` checks only that the Compose and Swarm `image` starts with `${GRAPHOS_IMAGE:?` (lines 83 and 126).

### Architecture

```
artifact path --> checker.check_file
                    |-- document has `kind`            --> existing Kubernetes walker (unchanged)
                    |-- document has `services` map    --> new compose/swarm walker
                    |        image literal --> same _valid_image
                    |        image ${VAR:?msg} --> template mode: accepted, counted as "pin-required"
                    |                              resolve mode (--set VAR=ref): ref checked by _valid_image
                    |-- helm: `helm template -f <pinned-values>` output --> Kubernetes walker
```

One script, one `_valid_image`, one exit contract (0 pass, 1 error, at least one image checked). The only additions are a second document walker and the interpolation rule. No second checker file is created.

### D1 Digest pinning by target (CONFIRMED by reading the artifacts)

| Target | Image references in the artifact | Pinned in the file today |
|---|---|---|
| `deploy/compose/compose.yaml` | One service, `graph-os`: `image: ${GRAPHOS_IMAGE:?set GRAPHOS_IMAGE ...@sha256:<digest>}` (line 21). The engine runs inside the same container; no other service. | No literal digest. Compose refuses to start when the variable is unset; nothing checks that its value carries a digest. |
| `deploy/swarm/stack.yml` | One service, `graph-os`: the same `${GRAPHOS_IMAGE:?...}` form (line 31). | Same: fail-closed on unset, digest not checked. |
| `deploy/helm/graph-os` | `graphos.image` serves the `graph-os` container and the `graph-os-daemon` container (`templates/graphos.yaml` lines 62 and 123, via `graph-os.image`); the engine container uses `graph-os.engineImage` (lines 34 and `engine.yaml` 26), which falls back to `graphos.image` when `engine.image.repository` is empty; every `connectors[]` and `components[]` entry renders its own `image` (`extensions.yaml` line 37). | No. `values.yaml` defaults are `tag: replace-me`, `digest: ""`; `_helpers.tpl` renders `repository@digest` only when `digest` is set, otherwise `repository:tag`. Default rendering is therefore tag-based and would fail the checker (the intended fail-safe). |

Environment profiles: `prod.yaml` and `test.yaml` declare `tag_policy: digest-pinned`; `dev.yaml` declares `floating-tag` (non-prod only). The profile policy and the artifact check are separate; this design makes the checker enforce it per target.

### D1 Decision (DECIDED)

Extend the existing script rather than add a checker. Compose/Swarm `${NAME:?...}` with no default is accepted in template mode because the runtime itself refuses an unset value; `${NAME:-default}` and literal images must be digest-pinned; `--set NAME=value` resolves the variable and the value must pass `_valid_image`. For Helm, CI renders the chart with a pinned values fixture and pipes the output to the same script; a second CI step renders defaults and expects exit 1. Alternative rejected: a separate compose linter (a second parser and a second pin grammar to keep in sync).

### D2 to D4 and D5

Dashboard parity, artifact authentication and the agent-utilities export are resolved in `specs/hosted-api-operations/plan.md` (GRAPHOS-OPS-R039 to R041).

### D5 systemd target (DECIDED: keep unsupported)

Evidence: all three profiles under `deploy/environments/` set `target.orchestrator: kubernetes` (`dev.yaml` line 25, `test.yaml` 26, `prod.yaml` 32), `genesis_environments.py` validates the orchestrator against `genesis.yaml` `run_plan.orchestrators`, and the production profile is Helm. The only systemd material in the repository is guidance: `graph_os/skills/graphos-genesis/references/bare-metal.md` and a Quadlet note in `compose-and-podman.md`; neither is a rendered artifact under `deploy/`. Decision: keep systemd unsupported as a placement artifact. A new artifact would need its own renderer or hand-kept unit file, a drift gate and a first-boot test with no profile that uses it, which is the parallel functionality the lane rules forbid. The reference stays operator guidance. Reopen condition: an `environments/*.yaml` profile with a non-kubernetes bare-metal orchestrator appears. Alternative: add `deploy/systemd/graph-os.service` with a unit-lint test; rejected for lack of a consumer.

R017.6 therefore refuses `systemd` with a typed `unsupported_target` carrying the pointer to the bare-metal reference, and R017.15 tests that.
