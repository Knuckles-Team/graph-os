---
name: graphos-genesis
skill_type: skill
description: >-
  Day-0, idempotent substrate provisioning for a Graph OS installation: a
  laptop or single host with Docker Compose (or Podman), an existing
  Kubernetes namespace, an existing cluster, or a newly provisioned
  multi-node cluster. Use for environment discovery, topology selection,
  container or cluster substrate, identity/secrets/PKI boundaries, Helm and
  GitOps foundations and workspace bootstrap, then hand off to
  graphos-deployment for the application layer. Do NOT use to configure or
  verify a running Graph OS (graphos-deployment) or to diagnose one
  (graph-runtime-and-governance).
---

# Graph OS Genesis

Create the smallest suitable substrate for the requested Graph OS
installation, prove it is ready, then invoke `graphos-deployment` to install
and verify the application.

This skill is environment-neutral. Never copy host names, addresses, storage
paths, registries, credentials, cluster names or DNS suffixes from examples.
Discover them, or require operator-owned references at run time. A private
overlay skill for a specific estate may layer site facts on top of this one;
when one is installed, follow it for those facts only.

## What is being deployed

Read [runtime-topology.md](references/runtime-topology.md) first. In one line:

- **graph-os** is the runtime: one process serving MCP, the REST/A2A control
  plane and the web UI, multiplexing the fleet of MCP connectors, and hosting
  identity and policy.
- **epistemic-graph (EG)** is the engine and sole durable authority. In the
  standard *unified* topology it runs in the same pod or container as graph-os,
  as a native sidecar container (Kubernetes) or a supervised child process
  (Compose, bare metal). Its store has exactly one writer.
- **agent-utilities (AU)** is the agent-orchestration library graph-os imports.
  It is not deployed as a service of its own.
- **Connectors** are MCP servers built on agent-connector-sdk. graph-os reaches
  them through its fleet multiplexer.

Features that are not on `main` yet are marked **available from <train>**.
Never describe such a feature as present on an install that predates it; check
the installed release first.

## Responsibility boundary

`graphos-genesis` owns Day-0 infrastructure:

- inventory and constraint discovery;
- a deploy / use-existing / skip decision per infrastructure capability;
- runtime selection and provisioning (Compose host, Podman host, Kubernetes);
- namespaces, service accounts, RBAC, network boundaries, storage classes,
  ingress, certificates, secret-store integration and GitOps substrate;
- placement, capacity, backup, restore and failure-domain planning;
- a sanitized `genesis-plan.yaml` and the deployment handoff.

`graphos-deployment` owns the application:

- deployment profile, identity mode, policy (Eunomia), secrets backend;
- configuration, runtime environment and secret references;
- graph-os and its engine, the connector fleet, skills, prompts, UIs;
- first-boot administrator, migrations, upgrades and first-boot verification.

**Mandatory delegation rule:** after Day-0 preflight succeeds, invoke
`graphos-deployment` with the resolved handoff. When a suitable substrate
already exists and no Day-0 change is needed, go straight to that skill. Do
not duplicate its configuration or verification here.

Read [deployment-contract.md](references/deployment-contract.md) before
changing infrastructure.

## Safety invariants

1. Default every capability to `use-existing` when a compatible managed service
   is reachable; otherwise `deploy`; use `skip` only when its dependants are
   also disabled.
2. Read and inventory before mutation. Render and validate before apply.
3. Never persist plaintext secrets in Git, Helm values, Compose files,
   generated plans, logs, the knowledge graph or agent context. Store only
   secret references.
4. Pin production images by digest and verify signatures/SBOMs where the
   registry supports them. Floating tags are development-only.
5. Never grant `cluster-admin` to an application or agent. Namespace-only mode
   stays namespaced.
6. Keep rollback available until health, data, identity, ingress and a
   delegated tool call pass from the user-facing route, including the browser
   sign-in path.
7. A plan is not a deployment. Report `planned`, `rendered`, `applied` and
   `verified` separately.
8. Make every action idempotent and record an idempotency key plus
   before/after evidence. Destructive replacement, data migration, DNS
   cutover or trust-root rotation needs explicit operator approval.
9. The engine store has one writer. Never run two graph-os pods, two
   containers or two engine processes against the same store.

## Workflow

Run the phases in order. Use the skill directly for a bounded plan or
preflight; delegate the dependency-ordered phases of a multi-node rollout to
sub-agents and keep every external change behind the same approval boundary.
Use an economy model for inventory classification, render checks and checklist
execution; escalate topology, security and recovery decisions to a stronger
model when evidence is ambiguous or the blast radius is high.

### Phase 0 — Resolve intent and scope

Collect or infer:

- mode: `evaluation`, `development`, `small-production` or
  `production-at-scale`;
- runtime: `compose` (Docker or Podman, one host), `bare-metal` (systemd),
  or `kubernetes`;
- Kubernetes authority: `namespace-only`, `existing-cluster`,
  `provision-cluster` or `provision-multi-node`;
- engine topology: `unified-in-process` (default) or
  `out-of-process-shared`; for unified on Kubernetes, engine placement
  `sidecar` (default) or `child`;
- identity mode the application will start in: `none`, `local` or `external`
  (decided with the operator here because it shapes ingress, IdP and secret
  needs; applied by `graphos-deployment`; identity modes are **available from
  train 7 (identity)** — before it, an existing OIDC issuer is required for any
  non-loopback install);
- identity, secrets, PKI, DNS/ingress, storage, observability, backup and
  GitOps providers, each `deploy`, `use-existing` or `skip`;
- selected connectors: `core`, named packages, a manifest filter, or `all`;
- optional data-plane services (object store, catalog, query engine, compute,
  streaming, triple store) — default every one to `skip`; read
  [data-plane-substrate.md](references/data-plane-substrate.md) before
  selecting any;
- tenancy, availability, recovery objectives, resource ceilings, egress
  policy, change window and approval boundaries.

"Enterprise" does not mean self-host everything. A namespace in a managed
cluster that reuses existing OIDC, a Vault-compatible store, ingress, storage
and observability is a first-class production target.

**Docker Swarm is not a supported target.** A Swarm estate migrates to Compose
(one host) or Kubernetes (several hosts); see
[compose-and-podman.md](references/compose-and-podman.md) and
[orchestrator-migration-cutover.md](references/orchestrator-migration-cutover.md).

### Phase 1 — Discover and preflight

Inspect without changing state:

- CPU architecture and instruction-set level (engine and connector images may
  need a minimum CPU baseline), cores, RAM, accelerators, disk capacity/IOPS,
  filesystem, network, MTU, time sync, cgroups and open ports;
- container runtime and Compose versions (Compose v2.24+ for optional
  `env_file` entries), or cluster version, API capabilities (native sidecars
  need Kubernetes 1.29+), quotas, LimitRanges, Pod Security, RBAC, CNI
  NetworkPolicy support, CSI, ingress/Gateway API, cert-manager, External
  Secrets, metrics API, topology labels and taints;
- OIDC discovery documents, secret-store auth methods, trust bundle, DNS
  authority, registry access, observability endpoints and backup target;
- image availability and architecture for graph-os and every selected
  connector;
- overlap among host, service, pod and VPN CIDRs;
- the current `workspace.yml`, selected packages and dependency order.

Classify every prerequisite `ready`, `degraded`, `missing` or `incompatible`,
with evidence and a remediation. Never mutate merely to discover.

### Phase 2 — Produce the deployment contract

Write a sanitized, operator-owned `genesis-plan.yaml` conforming to
[deployment-contract.md](references/deployment-contract.md): target and
authority, component names and source revision, per-capability action,
resources, placement, network/ingress/storage/identity/secrets/PKI/
observability/backup references, immutable images, ordered phases with health
gates, rollback and idempotency keys, the exact handoff, and redacted evidence
locations.

Resolve conflicts before execution: a skipped secret store cannot satisfy an
ExternalSecret; a namespace-only service account cannot install CRDs; a
`ReadWriteOnce` engine volume cannot serve two writers; identity mode `none`
cannot be exposed beyond one operator's loopback without the exact
acknowledgement.

### Phase 3 — Select the minimum substrate

| Situation | Runtime | Reference |
|---|---|---|
| Laptop evaluation, one developer | Compose (Docker or Podman) or bare metal | [compose-and-podman.md](references/compose-and-podman.md), [bare-metal.md](references/bare-metal.md) |
| One durable host | Compose, rootful Podman + Quadlet, or systemd | [compose-and-podman.md](references/compose-and-podman.md), [bare-metal.md](references/bare-metal.md) |
| Developer changing many repositories | bare metal (editable installs) | [development-workspace.md](references/development-workspace.md) |
| Existing namespace, no cluster administration | Kubernetes `namespace-only` | [kubernetes-and-helm.md](references/kubernetes-and-helm.md) |
| Existing cluster with platform administration | Kubernetes `existing-cluster` | [kubernetes-and-helm.md](references/kubernetes-and-helm.md) |
| New single-node or edge cluster | Kubernetes `provision-cluster` | [kubernetes-and-helm.md](references/kubernetes-and-helm.md) |
| Several hosts, HA, several failure domains | Kubernetes `provision-multi-node` | [kubernetes-and-helm.md](references/kubernetes-and-helm.md) |
| An existing Swarm estate | migrate to Compose or Kubernetes | [orchestrator-migration-cutover.md](references/orchestrator-migration-cutover.md) |
| Lakehouse, streaming or triple-store services | optional data plane, Kubernetes | [data-plane-substrate.md](references/data-plane-substrate.md) |

Do not provision Kubernetes merely because it is available. Choose it when its
scheduling, policy, availability, GitOps or tenancy benefits justify the cost.

### Phase 4 — Provision or attach

**Compose / Podman.** Follow
[compose-and-podman.md](references/compose-and-podman.md). The graph-os source
tree ships `deploy/compose/compose.yaml`: one graph-os service with the engine
as a supervised child, ports published on `127.0.0.1`, one data volume.
Validate with `docker compose config --quiet` (or `podman compose config`).

**Kubernetes.** Follow [kubernetes-and-helm.md](references/kubernetes-and-helm.md).
The graph-os source tree ships the chart at `deploy/helm/graph-os`: unified or
shared topology, the engine as a native sidecar or child, namespaced RBAC,
PVC, probes, PDB, HPA (shared only), split API and web UI ingress,
NetworkPolicy, fleet connectors from values, and existing-Secret references
only. It refuses to render identity mode `none` without the exact
acknowledgement, and a TCP engine listener without TLS. Cluster bootstrap is
provider-pluggable (managed service, Cluster API, kubeadm, RKE2, k3s, Talos or
an approved equivalent); record the provider and version and prove the same
postconditions.

**Bare metal.** Follow [bare-metal.md](references/bare-metal.md): a dedicated
service account and environment, hardened systemd units, state outside the
checkout.

**Optional data plane.** Each selected service is a sibling artifact applied
with its own manifest, never a chart template. The engine consumes external
databases as **federation sources**, not mirrors — see
[data-plane-substrate.md](references/data-plane-substrate.md).

### Phase 5 — Establish cross-cutting services

Follow [security-and-operations.md](references/security-and-operations.md):

1. workload identity and least-privilege authorization;
2. the secrets backend: the engine's encrypted secrets graph for tiny and
   single-host installs, or OpenBao / another Vault-compatible store for
   production — plus the delivery path (External Secrets, CSI, env file);
3. trust roots and service certificates, including the engine TCP listener
   certificate when connectors must reach the engine;
4. ingress/Gateway API with TLS for the API and the web UI hostnames;
5. storage, snapshots, backup and a restore drill;
6. logs, metrics, traces, alert routing;
7. egress allow-list and tenant/network isolation;
8. GitOps ownership and drift detection.

Unless graph-os starts the engine itself (`child` placement), provision the
engine signer key for graph-os's own principal as its own step — in the runtime
Secret for `sidecar`, through the full chain for the shared topology. Read
[engine-identity-admission.md](references/engine-identity-admission.md) end to
end first.

### Phase 6 — Bootstrap a development workspace

Only for `mode: development`: follow
[development-workspace.md](references/development-workspace.md) — clone the
supplied repository, resolve the canonical `workspace.yml`, run the
manifest-driven clone, install selected repositories in dependency order, and
delta-ingest sources once graph-os is healthy.

### Phase 7 — Mandatory application handoff

Invoke `graphos-deployment` with:

```yaml
deployment_profile: <tiny|single-node-prod|enterprise>
run_target: <compose|bare-metal|kubernetes>
substrate_resolved: true
namespace: <namespace-or-null>
engine_topology: <unified-in-process|out-of-process-shared>
engine_placement: <sidecar|child|null>
identity_mode: <none|local|external>
components: [<resolved component names>]
providers:
  identity_ref: <reference-or-null>        # external IdP discovery reference
  secrets_backend: <engine|vault>
  secrets_ref: <reference>
  trust_bundle_ref: <reference-or-null>
  ingress_class: <name-or-null>
  storage_class: <name-or-null>
  observability_ref: <reference-or-null>
  data_plane_ref: <reference-or-null>
artifacts:
  helm_values: <path-or-null>
  compose_project: <path-or-null>
  inventory: <path>
constraints:
  namespace_scoped: <true|false>
  immutable_images: <true|false>
```

If `graphos-deployment` reports a missing substrate capability, resume genesis
in `substrate-only` mode for that bounded prerequisite and re-invoke it. Never
recurse into a full genesis run.

### Phase 8 — Exit gates

Do not declare success until every applicable gate passes:

1. rendered artifacts contain no unresolved placeholders or plaintext secrets
   (`helm lint`, `helm template`, `docker compose config --quiet`);
2. policy/schema validation and a server-side dry run pass;
3. workloads meet readiness, resource, placement and restart checks; the
   engine store survives a controlled restart;
4. a backup restore has been tested;
5. `graphos-deployment` returned its first-boot verification evidence,
   including a real browser sign-in (see its first-boot checklist) — health
   endpoints alone never pass this gate;
6. where graph-os admits to an engine it did not start (sidecar or shared
   topology), the admission chain is proven, not merely deployed: follow
   "Verifying it works" in
   [engine-identity-admission.md](references/engine-identity-admission.md) — a
   control-graph read that failed with `CypherEngineError(PermissionError)`
   now succeeds;
7. rollback is executable and evidence is stored without credentials.

Distinguish claims: an `Agent.run` tool loop, a multi-node `pydantic_graph`
run and a harness workflow are three separate validations; one does not
prove the others.

## Output

Return the resolved plan and component matrix; what was used, provisioned,
skipped or blocked; immutable artifact and revision identifiers; the
`graphos-deployment` handoff and result; per-gate evidence links; rollback
instructions; and deferred work with owner and reason. Never report an
unexecuted recipe as live or a partial trace as end-to-end validation.

Troubleshooting: [TROUBLESHOOTING.md](references/TROUBLESHOOTING.md).
