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

## GRAPHOS-DEPLOY-R017 plan: backend deployment planner (carried over from agent-utilities)

Replaces the agent-utilities module for multi-backend deployment planning and its `deploy-plan` console command, deleted by agent-utilities commit `db29b2da6` (agent-utilities row AU-BOUNDARY-R002.2, which removed the CLI parser, handler and dispatch entry, the module's unit test and a live-path test). The deletion text names graph-os as the sole owner of "the packaging entry point for rendering a DeploymentPlan across backends". Source inspection (2026-10-10) found no such entry point in graph-os yet: `deploy/compose`, `deploy/helm`, `deploy/swarm` and `deploy/environments` hold static artifacts, and `graph_os.deployment.genesis_environments` validates profiles, but nothing renders a reviewable, data-only plan per backend. This section therefore specifies the build.

### Functional description

The old capability answered one operator question: where should the single self-composing `graph-os` process run, and what exactly would that take? It produced a deterministic, reviewable `DeploymentPlan` and never mutated a remote host or cluster.

- **Plan model.** `FleetToolCall` (server, tool, args, description) is data that is never dispatched by the planner. `DeploymentStep` carries a description plus either a fleet call or a local action name. `DeploymentPlan` carries backend, target, composition, ordered steps, a `live_capable` flag, warnings and rendered artifacts (file name to content), and renders a text summary.
- **Composition input.** Every backend reads the same composition detection (messaging platforms, web UI flag, messaging intake only for the host daemon role), so all backends agree on what co-services are configured.
- **`in_process` backend.** Plan: run the `graph-os` console script. Apply with dry run returns the plan unapplied. Apply live requires a verified session (otherwise `ValueError`), starts the composed co-services in the calling process and returns the supervisor; the messaging-intake flag threads through. The only live-capable backend.
- **`container` backend (docker/podman).** Plan-only. Renders a compose file with one `graph-os` service (streamable-http, port 8000, restart unless-stopped) plus an `agent-webui` service only when the web UI is composed, and a step for the container-manager `cm_compose_operations` call (action up, compose file, host, manager type).
- **`kubernetes` backend.** Plan-only. Renders a Deployment and Service (plus a web UI Deployment when composed) and one real step (`cm_k8s_config` create_namespace). It states there is no generic manifest-apply tool, so the manifest is for GitOps review.
- **`native_shell` backend (systemd).** Plan-only. Renders a systemd unit (Restart on-failure, RestartSec 5, wanted by multi-user) and two steps through tunnel-manager `tm_remote` run_command (write the unit and daemon-reload; enable and start). The target is an inventory host alias.
- **Refusal.** `apply(dry_run=False)` on the three plan-only backends raises `PlanOnlyBackendError`; it never silently no-ops. `get_backend(name)` raises `ValueError` listing known names for an unknown backend.
- **`deploy-plan` command.** Required `--backend` (one of the four), `--target` (default "this process"), repeatable `--param KEY=VALUE` overrides (image, namespace, manager type and so on; entries without `=` silently ignored). Printed a JSON object: backend, target, live_capable, composition names, steps (description, fleet call server/tool/args, local action), warnings, artifacts. Plan-only except in_process; never applied anything remote.
- **Callers.** The only production importer was the CLI command. Documentation strings in the doctor and the composition module pointed at the backends module for the "Kubernetes deploys stay plan-only" posture.

### Design in this repository

- New module graph_os/deployment/backends.py (plan model, backend classes, renderers, registry), split further only if the CCCC gate requires it. It reuses `graph_os.mcp_server.composition.detect_composition`, `CompositionPlan` and `start_composed_services`, and does not redeclare composition logic.
- Typed models are frozen dataclasses or pydantic models with validation: backend is a closed enum, target and image are non-empty, and an image reference must be digest-pinned for the `container` and `kubernetes` backends. Deliberate divergence from the old planner, which defaulted to a floating `latest` tag; this repository's requirement GRAPHOS-DEPLOY-R001 and the profile rules in this spec refuse floating production images, so image has no default there.
- Renderers emit YAML through `yaml.safe_dump`; secrets appear only as references, never values, and no private endpoint or inventory content enters an artifact.
- Where the profile model applies, the planner accepts an already validated `EnvironmentProfile` from `graph_os.deployment.genesis_environments` as the source of image, namespace, port and target, instead of free-form `--param` keys. Free-form `--param` stays only as the compatibility surface.

### Wiring

- CLI: a new `deploy-plan` subcommand of `graph_os.deployment.cli.main` (the `setup-config` console script), registered next to the `environments` and `release` subparsers, calling a handler that prints the JSON structure above. No new console script, so no new collision with the R007.x scripts.
- Library: `get_backend` is importable from the deployment package for the doctor's plan-only prescriptions; the doctor's wording about plan-only posture points at this module.
- Config keys: none new. Composition keys are the existing web UI and messaging settings read by `detect_composition`. Scopes: none; the command is read-only and `in_process` live apply requires the caller's verified session. No new engine scope is introduced.

### Parity table

| Old entry point | New entry point | Status |
|---|---|---|
| `FleetToolCall`, `DeploymentStep`, `DeploymentPlan`, `rendered_summary` | plan model in the new backends module | to build |
| `DeploymentBackend` protocol, `get_backend`, unknown-name `ValueError` | same module | to build |
| `PlanOnlyBackendError` | same module | to build |
| `detect_composition` / `CompositionPlan` (agent-utilities supervisor) | `graph_os.mcp_server.composition` | exists (verify with a test) |
| `InProcessBackend.plan` / `.apply` | in_process backend over `start_composed_services` | to build (supervisor start exists) |
| `render_compose`, `ContainerBackend` | container backend | to build; static compose exists in `deploy/compose` but is not generated |
| `render_k8s_manifest`, `KubernetesBackend` | kubernetes backend | to build; Helm chart exists in `deploy/helm` and stays the production path |
| `render_systemd_unit`, `NativeShellBackend` | native_shell backend | to build; only prose in the genesis skill references exist |
| `deploy-plan` CLI (`--backend`, `--target`, `--param`) | `setup-config deploy-plan` | to build |
| Default floating `latest` image | none | intentionally dropped (digest-pinned images required) |
| `python -m agent_utilities.cli graph-os` as systemd ExecStart | the `graph-os` console script | intentionally dropped (the old module path no longer exists) |
| Silent ignore of a `--param` without `=` | exact diagnostic | intentionally changed (fail with the offending token) |

### Out of scope

Executing any plan-only fleet call; container-manager, tunnel-manager and systems-manager tool contracts (owned by those repositories); replacing `deploy/helm` or the profile renderers required by GRAPHOS-DEPLOY-R003/R013; Swarm planning (the old planner never covered it).
