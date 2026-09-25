# Orchestrator migration and cutover

Migrate one dependency-closed application unit at a time. Genesis owns the target
substrate; `graphos-deployment` owns application render, migration, and
verification.

## Before change

- inventory the source artifact, configuration, secret references, identity, DNS,
  certificates, networks, volumes, database/data format, queues, and external
  dependencies;
- capture traffic, error, latency, and data-integrity baselines;
- build and validate target artifacts without applying them;
- prove backup and restore;
- define freeze/dual-write semantics and rollback boundary;
- lower DNS TTL only when DNS cutover is selected;
- require explicit approval for data mutation and traffic cutover.

## Sequence

1. Provision/attach the target substrate.
2. Deploy stateful dependencies without sending production traffic.
3. Quiesce or checkpoint source writers according to the data contract.
4. Migrate through supported logical export/import or replication; do not copy live
   database files between incompatible runtimes.
5. Verify counts, hashes, constraints, graph queries, and application-level reads.
6. Deploy application at zero/limited traffic and run internal health/auth/tool tests.
7. Canary user traffic and compare traces, outputs, cost, and latency.
8. Cut over the declared routing layer.
9. Stop or fence the old writer; verify with it unavailable.
10. Observe through the rollback window, then retire old resources separately.

Preserve canonical issuer/audience and external service names where possible. A
post-cutover authorization failure may be stale DNS or a gateway token route, not a
bad credential.

## From Docker Swarm

Swarm is not a supported Graph OS target. Treat a Swarm estate as the source of
an ordinary cutover:

- one host → the Compose project (`deploy/compose`); several hosts →
  Kubernetes with the chart (`deploy/helm/graph-os`);
- move secrets out of Swarm secrets into the selected backend (engine secrets
  graph or a Vault-compatible store) as references, never by copying values
  into files;
- stop the Swarm graph-os service and wait until its task has fully exited
  before the new unit opens the engine store — the store holds a single-writer
  lock for the life of the process;
- copy the engine store with the writer stopped (or through the engine's
  backup/restore), never live;
- repoint connectors that dialled the old engine address to the new engine
  listener (TLS) or through graph-os;
- keep the Swarm stack definition, scaled to zero, until the rollback window
  closes, then retire it separately.

## Rollback

Rollback requires an exact prior artifact, compatible data state, routing action, and
owner. If writes reached the new system, execute the declared reverse replication or
forward-fix plan; never point the old binary at a newer incompatible store.

## Exit gates

User entrypoint, graph-os, model execution, selected tool, connector, engine
transaction, and trace must all pass through the target with the source disabled.
Verify persistence, identity, isolation, background checkpoint/resume, alerts, and a
second idempotent apply.
