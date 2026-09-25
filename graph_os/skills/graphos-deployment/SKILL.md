---
name: graphos-deployment
skill_type: skill
description: >-
  Plan, install, configure, verify, upgrade, migrate or recover a Graph OS
  runtime (graph-os with its epistemic-graph engine, the connector fleet and
  the web UI) from a tiny single-host profile to a Kubernetes production
  profile. Use when the deployment profile, identity mode, policy, secrets
  backend, federation sources, connectors, telemetry or installation state may
  change, and to run first-boot verification. Use graphos-genesis first when
  hosts, clusters or namespaces must be created or changed; use
  graph-runtime-and-governance for incidents that need no deployment change.
---

# Graph OS deployment

Resolve the deployment from requirements, produce a reviewable plan, apply only
authorized steps, and verify the user-visible path: a real browser sign-in, an
MCP session and an API call reaching the same backend.

Environment-neutral: never embed host names, addresses, registries, realm names
or paths. A private overlay skill for a specific estate may supply those; when
one is installed, follow it for site facts only.

Features that are not on `main` yet are marked **available from <train>**.
Check the installed release before relying on one; never report such a
behaviour as present on an older install.

## Genesis handoff

When `graphos-genesis` supplies an infrastructure handoff, validate and consume
it instead of re-discovering the substrate: plan digest, runtime and
permission boundary, namespace, engine topology and placement, identity mode,
provider references, component selection, resource ceilings and rendered
artifact locations. Set `substrate_resolved=true`.

If a required infrastructure capability is missing, call `graphos-genesis` in
bounded `substrate-only` mode and resume with its updated handoff. Never
restart a full genesis/application run recursively. For an application change
on a suitable existing substrate, run this skill directly.

## Workflow

Use the skill directly for a bounded plan, preflight or health check. Delegate
the dependency-ordered phases of a multi-node rollout or recovery to
sub-agents, keeping every external change behind one approval boundary. Use an
economy model for inventory classification, configuration comparison and
checklist execution; escalate identity, security and recovery decisions when
evidence is ambiguous or the blast radius is high.

### 1. Gather requirements

Confirm users, tenants, workload, durability and recovery objectives; one host
or a cluster; existing identity provider, secret store, ingress, databases and
observability; the allowed mechanism and change window; the connectors and
entrypoints in scope. Do not infer permission to create external
infrastructure or rotate credentials.

### 2. Select the profile and the modes

| Profile | Runtime | Identity mode | Eunomia | Secrets backend |
|---|---|---|---|---|
| `tiny` | one host, loopback | `none` | off (`none`) | engine secrets graph |
| `single-node-prod` | Compose, Docker Swarm or bare metal | `local` | on (`embedded`, shipped policy) | engine secrets graph, or OpenBao |
| `enterprise` | Kubernetes | `local`, then usually `external` | on (`embedded` or `remote`) | OpenBao / Vault-compatible |

Rules that hold in every profile:

- the identity mode seeds durable engine state on first boot only; afterwards
  the stored mode wins and changes only through a mode transition;
- Eunomia, when on, **fails closed**: an unreachable policy decision point
  hides the fleet and refuses calls, it never exposes them;
- no secret value appears in configuration — only references
  (`engine://…`, `vault://…`, `env://…`, a Secret name).

Read [identity-and-access.md](references/identity-and-access.md) before
choosing or changing any identity setting, and
[secrets-and-federation.md](references/secrets-and-federation.md) before
choosing a secrets backend or connecting an external database.

### 3. Preflight and plan

- Generate configuration from the canonical schema (`setup-config generate
  --profile <profile>`), never a hand-written partial file; run
  `setup-config doctor --profile <profile>`.
- For Kubernetes, render the chart with operator values; for one host, fill
  the Compose `.env` and `graph-os.env`; for Swarm, create the Swarm secret and
  label the data node, then validate the stack (see `graphos-genesis`).
- Resolve each dependency as deploy, reuse or skip.
- Present the plan, destructive steps, rollback and verification before
  apply.

### 4. Deploy in dependency order

1. secrets backend and delivery (the runtime Secret: engine HMAC secret,
   engine secrets, optional setup code, signer keys where the topology needs
   them);
2. graph-os and its engine (one unit in the unified topology);
3. first administrator and identity mode (below);
4. policy (Eunomia document, when on) and service identities with their exact
   scopes;
5. observability: OTLP export, metrics scrape of graph-os `/metrics`, browser
   RUM relay — see [telemetry.md](references/telemetry.md);
6. fleet: connectors, the multiplexer configuration and federation sources —
   see [fleet-and-control-plane.md](references/fleet-and-control-plane.md);
7. optional scheduled ingestion and background loops (propose-only by default).

Stop a dependent stage when its prerequisite is unhealthy. Use canary rollout
for multi-node or unfamiliar components.

### 5. Create the first administrator

- `none` (tiny): the bootstrap administrator exists from first boot; nobody
  signs in. Leave `none` later with `graph-os-identity claim --username <name>`.
- `local` / `external`: a fresh instance is unusable until its first
  administrator exists. Open `/auth/setup` in the web UI and enter the
  one-time setup code — `GRAPHOS_SETUP_CODE` from the runtime Secret, or the
  code graph-os writes to its log at start. Only someone who can read the
  Secret or the log can claim the instance.

Available from train 7 (identity). Before it, the first administrator is the
IdP user holding `kg:admin` (see identity-and-access).

### 6. Verify

Run [first-boot-verification.md](references/first-boot-verification.md) end to
end. It probes the **browser sign-in path**, the MCP session, the API control
plane, a delegated tool call, the engine store across a restart, and the
policy/secret wiring. Health endpoints alone never verify a deployment. Record
profile, component status, evidence and remaining operator actions.

### 7. Upgrade, migrate or recover

Classify the change first:

- a **release upgrade** replaces images or packages without changing stored
  data;
- a **configuration migration** renders the current typed schema from
  operator-owned references; runtime code keeps no retired keys or aliases;
- a **stored-format migration** transforms durable state once at the
  deployment boundary with the engine's supported hook; no permanent
  read-old/write-new path;
- an **ontology or schema change** belongs to `graph-modeling-and-mutation`;
  coordinate its ordering here;
- an **orchestrator migration** (between Compose, Swarm and Kubernetes) delegates the
  target substrate to `graphos-genesis`, then migrates each dependency-closed
  unit with canary traffic and an exact rollback.

Capture health and version state, back up durable data and test the restore,
stage the change, keep a rollback. The engine store has one writer: stop the
old unit completely before the new one opens the store. A binary rollback is
insufficient after a stored-format change. Repeat the first-boot verification
after every upgrade or migration.

## Guardrails

- Never store or print credentials, tokens, private keys, setup codes or
  recovery material.
- Never weaken authentication, policy or the `none`-mode guard rails to pass a
  health check; never switch Eunomia off to make discovery "work".
- Never grant the graph-os service identity `kg:admin` or an approver scope.
- Do not keep compatibility shims, retired configuration names or dual-format
  readers after a migration boundary.
- Do not claim success while a required doctor check or a first-boot step is
  failing.
- Require explicit approval for destructive data, identity, network or
  external-service changes, and for every identity mode transition.

Troubleshooting: the genesis skill's TROUBLESHOOTING reference and
[identity-and-access.md](references/identity-and-access.md) (symptom table).
