# Portable deployment contract

Genesis resolves intent into a sanitized contract before it changes
infrastructure. The contract is operator-owned and contains references, never
credential values.

## Minimal schema

```yaml
schema_version: 1
operation: plan # plan | apply | verify | migrate | recover
source:
  repository_url: <operator-supplied URL>
  revision: <immutable commit or release>
  workspace_manifest: workspace.yml
scope:
  mode: runtime # runtime | development | both
  selectors: [core]
  include: []
  exclude: []
target:
  runtime: kubernetes # compose | swarm | bare-metal | kubernetes
  ownership: use-existing # use-existing | provision
  authority: namespace-only # namespace-only | cluster-admin | host-admin
application:
  profile: enterprise # tiny | single-node-prod | enterprise
  identity_mode: local # none | local | external
capabilities:
  identity: {action: use-existing, provider_ref: <reference>}
  secrets: {action: use-existing, backend: vault, provider_ref: <reference>}
  pki: {action: use-existing, provider_ref: <reference>}
  ingress: {action: use-existing, provider_ref: <reference>}
  storage: {action: use-existing, provider_ref: <reference>}
  observability: {action: use-existing, provider_ref: <reference>}
  gitops: {action: skip, provider_ref: null}
  data_plane: [] # optional named services, see data-plane-substrate.md
topology:
  engine: unified-in-process # unified-in-process | out-of-process-shared
  engine_placement: sidecar # sidecar | child (unified only; child on compose/swarm)
  high_availability: false
  failure_domains: []
artifacts:
  images: {}
  inventory_ref: <path-or-object-reference>
approvals:
  destructive: false
  cutover: false
```

Each selected component expands into a dependency-closed record with `action`,
`provider`, immutable artifact, resources, placement, health gate, rollback and
idempotency key. Record a stable digest of the resolved plan.

`capabilities.data_plane` is a list of independently optional services, each
with `action: deploy | use-existing | skip` and a `provider_ref`. Default every
entry to `skip`; a minimal profile resolves with an empty or all-`skip` list.

Consistency rules the plan must satisfy before apply:

- `identity_mode: none` requires `runtime: compose` or `bare-metal`, a
  loopback-only publish, and the exact exposure acknowledgement when the
  process binds a non-loopback address (every container does);
- `identity_mode: external` requires `capabilities.identity` with a reachable
  OIDC/SAML/LDAP provider;
- `secrets.backend: vault` requires a reachable Vault-compatible store and a
  delivery path; `engine` requires nothing external;
- `runtime: swarm` requires `engine: unified-in-process`, `engine_placement:
  child`, exactly one node labelled for the data volume, and a recorded
  rotation owner for Swarm secrets;
- `engine: out-of-process-shared` requires an engine TLS certificate and a
  provisioned signer key for graph-os.

## Infrastructure handoff

The handoff to `graphos-deployment` contains:

- plan digest and source revision;
- runtime and permission boundary; namespace or service account;
- registry, storage class, ingress class, DNS zone and trust-bundle references;
- identity mode, identity issuer/client references and secret-store references;
- resolved data-plane endpoint/auth references, when any are selected;
- selected component names, resource ceilings and topology;
- rendered artifact locations (Helm values, Compose project);
- preflight evidence and unresolved administrator requirements.

Set `substrate_resolved: true` on the application call. If the application
deployment asks genesis for a missing prerequisite, genesis runs
`substrate-only` and returns a new handoff; it never deploys the application.

## State labels

- `planned`: a validated contract exists.
- `rendered`: runtime artifacts validate offline.
- `applied`: the target accepted the artifacts.
- `verified`: the user-visible route (browser sign-in included) and the
  persistence gates passed.
- `blocked`: a named missing capability or approval prevents the next step.

Never collapse these labels into a single "done".
