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
