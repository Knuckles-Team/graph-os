# Runtime topology

## The four parts

| Part | Role | Deployed as |
|---|---|---|
| graph-os | The runtime: MCP server (intent verbs + fleet multiplexer meta-tools), REST/A2A control plane, web UI, identity broker, policy (Eunomia), fleet gateway, background daemons | the main process/container |
| epistemic-graph (EG) | The engine and sole durable authority: graph, SQL catalog, RBAC, identity store, secrets graph, federation, audit | same pod/container as graph-os (unified) or a separate service (shared) |
| agent-utilities (AU) | Agent-orchestration library (agents, harness, workflows) imported by graph-os | a Python dependency only; no service, no port |
| connectors | MCP servers built on agent-connector-sdk | separate processes/pods reached through the fleet multiplexer |

graph-os is the only public surface. Nothing else should be exposed to users.

## Unified topology (standard)

One unit owns graph-os and its engine. This minimizes moving parts and keeps
one identity, one lifecycle and one hostname set.

- **Engine placement `sidecar` (Kubernetes default).** The engine runs as a
  native sidecar container (`initContainers` entry with
  `restartPolicy: Always`) in the graph-os pod, started from the same unified
  image. graph-os connects over a Unix socket on a shared `emptyDir`
  (`GRAPH_SERVICE_ENDPOINTS=unix:///run/epistemic-graph/epistemic-graph.sock`).
  The engine has its own resources, restarts and logs, and starts before
  graph-os. graph-os admits to it as a client, so the engine signer key for
  graph-os's principal goes in the runtime Secret both containers read (see
  [engine-identity-admission.md](engine-identity-admission.md)).
- **Engine placement `child` (Compose and bare metal default).** graph-os starts
  and supervises the engine binary inside its own container or service
  (`GRAPH_SERVICE_ENDPOINTS=""`, `GRAPH_SERVICE_PERSIST_DIR` set explicitly,
  `ENGINE_LIFECYCLE=persistent` so the engine never idles out while connectors
  depend on it). graph-os generates the engine bootstrap signer key and
  injects it, so a single host needs no operator signer step.

Rules for both placements:

- replicas: exactly one; `Recreate` rollouts only (the store holds a
  single-writer lock for the life of the process);
- storage: one durable single-writer volume; set the persist directory
  explicitly — an unset directory defaults under ephemeral storage and loses
  data silently;
- scaling: add resources, or move to the shared topology;
- upgrade: checkpoint, stop, migrate if required, replace, verify;
- metrics: the engine only binds its metrics listener on loopback; graph-os
  merges that exposition into its own `/metrics`;
- connectors that must reach the same engine use its TLS TCP listener
  (`engine.tcp.enabled` in the chart) with signed envelopes, never a second
  engine.

## Shared topology

Stateless graph-os replicas connect to a separately operated engine service
(`out-of-process-shared`). Choose it only when several graph-os replicas are
genuinely required.

- graph-os replicas may scale on concurrency/latency once session and cache
  safety are proven; engine replicas/partitions follow the engine's own
  consensus and sharding contract;
- every non-loopback engine listener requires TLS;
- graph-os admits to a remote engine with its own signer key — provision it
  per [engine-identity-admission.md](engine-identity-admission.md);
- retry budgets must prevent retry storms; cross-shard writes need the
  engine's transactional semantics, never filesystem sharing.

## Images

The Compose file and the chart consume an operator-supplied **unified image**:
graph-os, agent-utilities, agent-connector-sdk, the web UI extra, and the
`epistemic-graph-server` binary at a matching release. graph-os does not
publish an authoritative image of its own; the deploying organization builds
it from released artifacts and pins it by digest. Check the CPU baseline of
the engine binary against every node it can run on.

## Deployment profiles

| Profile | Shape | Identity default | Secrets default |
|---|---|---|---|
| `tiny` | one process on loopback, zero external infrastructure | `none` | engine secrets graph |
| `single-node-prod` | one durable host (Compose or bare metal) | `local` | engine secrets graph or OpenBao |
| `enterprise` | Kubernetes, reuses existing identity/secrets/ingress/observability | `local`, usually moved to `external` | OpenBao / Vault-compatible |

## Throughput rules

- batch across the Python/Rust or network boundary;
- separate model concurrency from disk, connector and reasoning queues;
- measure p50/p95/p99 latency, queue time, batch size, disk IO, memory and
  retries per lane;
- checkpoint background ingestion/evolution and yield to foreground users;
- scale only after a representative load test proves improved throughput
  without violating correctness, isolation, tail latency or recovery goals.
