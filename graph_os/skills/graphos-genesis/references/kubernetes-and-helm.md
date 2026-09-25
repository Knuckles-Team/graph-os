# Kubernetes and Helm

One postcondition contract across a managed cluster, an existing cluster or a
new one. Distribution-specific steps are adapters, not workflow semantics.

## Authority modes

### Existing namespace (`namespace-only`)

- Use the supplied namespace and service account.
- Create only namespaced resources allowed by RBAC.
- Do not install CRDs, ClusterRoles, admission controllers, CNI, CSI, ingress
  controllers, cluster issuers or cluster-wide operators.
- Preflight those capabilities and emit a precise administrator requirement
  when one is missing (e.g. "an ExternalSecret store named X", "ingress class
  Y", "Kubernetes 1.29+ for native sidecars").

### Existing cluster with platform authority (`existing-cluster`)

- Create a dedicated namespace and service accounts.
- Install shared operators only when selected and absent or incompatible.
- Keep application workloads namespaced and least-privileged.

### New cluster (`provision-cluster`, `provision-multi-node`)

Select an approved managed service, Cluster API provider, kubeadm, RKE2, k3s,
Talos or equivalent. Pin versions. Inventory nodes; choose non-overlapping pod
and service CIDRs, CNI (with NetworkPolicy), CSI, control-plane endpoint,
ingress, load balancing, registry trust and failure domains. A multi-node
production control plane uses an odd number of voters across failure domains.
Record node CPU baselines: images compiled for a newer instruction set crash on
older nodes, so label nodes and constrain placement accordingly.

After bootstrap, run the same existing-cluster preflight. Provider installation
does not prove substrate readiness.

## The chart

The chart is at `deploy/helm/graph-os` in the graph-os source tree (use the tree
at the release being deployed). Render before apply:

```bash
helm lint <chart> --strict --values <operator-values>
helm template <release> <chart> --namespace <namespace> \
  --values <operator-values> > <rendered>
kubectl apply --dry-run=server --namespace <namespace> -f <rendered>
```

Use `--create-namespace` only when the contract grants namespace creation.

### Values that matter

| Value | Meaning |
|---|---|
| `topology` | `unified-in-process` (default, one pod, one writer) or `out-of-process-shared` (engine StatefulSet + graph-os replicas behind an HPA) |
| `engine.placement` | unified only: `sidecar` (default; native sidecar container, Unix socket on a shared `emptyDir`) or `child` (graph-os supervises the engine in its own container) |
| `engine.image` | empty = reuse the graph-os unified image; set it for the shared StatefulSet |
| `engine.tcp.enabled`, `engine.tls.secretName` | expose the engine's TLS listener to in-cluster connectors; required TLS for any non-loopback listener |
| `identity.mode` | `none`, `local` (default) or `external`; seeds the durable mode on first boot. `none` refuses to render without `identity.noneExposeAck` set to the exact acknowledgement |
| `identity.issuer`, `identity.tenant` | the issuer URL the engine and clients trust, and the engine tenant |
| `policy.eunomia.type` | empty derives from the mode: `none` → `none`, `local`/`external` → `embedded`; `remote` needs `remoteUrl` |
| `secrets.backend`, `secrets.vaultUrl` | `vault` (OpenBao or another Vault-compatible store, default) or `engine` |
| `telemetry.*`, `capacity.prometheusUrl` | OTLP export, browser RUM relay, throttle-controller metrics source |
| `runtimeSecret.name` | the existing Secret both containers read (engine HMAC secret, engine secrets, optional setup code) |
| `ingress.api`, `ingress.webUi` | separate hosts for MCP/REST/A2A and for the browser UI |
| `graphos.probeHostHeader` | Host header for kubelet probes; it must be an allowed host, or probes get `400 host rejected` |
| `graphos.extraVolumes`, `graphos.extraVolumeMounts` | Eunomia policy file, trust bundles |
| `connectors` | fleet MCP connector Deployments/Services |

The chart accepts only references to an existing Secret. Create or synchronize
it through the selected secret provider before installation. Production values
pin image digests. Every pod sets `enableServiceLinks: false`, because
Kubernetes service-link variables collide with runtime configuration names.

### Rules

- Never raise the unified workload above one replica; the chart forces 1 and
  `Recreate`. Its PDB allows zero available: a drain stops the service.
- The engine's metrics listener is loopback-only by design; scrape graph-os's
  `/metrics`, which carries the engine series. Do not add a proxy sidecar.
- Choose `child` placement only when the cluster predates native sidecars
  (Kubernetes < 1.29) or the operator wants one container; `sidecar` gives the
  engine its own restarts, resources and logs.
- A large store recovers for minutes on a cold start; the chart's startup
  probes allow 30 minutes. Keep that budget when overriding probes.
- Connectors and extra components are data-driven lists; their dependency
  closure and configuration come from `graphos-deployment`.

## Required preflight

- Kubernetes version (1.29+ for native sidecars) and API versions used by the
  chart;
- namespaced RBAC for get/list/watch/create/update/patch/delete of the kinds;
- Pod Security and admission policies (the chart runs non-root, read-only root
  filesystem, all capabilities dropped);
- a StorageClass and access modes for one `ReadWriteOnce` volume;
- CNI NetworkPolicy support; ingress class or GatewayClass and a certificate
  mechanism;
- the metrics API before enabling an HPA (shared topology only);
- node architectures and CPU baselines, topology labels, taints, quotas,
  LimitRanges; registry pull and signature policy;
- DNS, MTU, NTP and egress to the declared providers.

## Production gates

Chart schema validation, lint, template, policy checks, server dry run,
rollout status, probes, PDB/HPA checks, NetworkPolicy tests, persistence across
a restart, backup and restore, and an external user-route test (browser
sign-in). Re-run `helm upgrade --install` with the same values and require no
unintended drift.
