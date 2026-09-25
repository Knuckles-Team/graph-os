# Docker Swarm

A supported flavor for an estate that already runs Docker Swarm across several
hosts and does not want Kubernetes. The graph-os source tree ships the stack at
`deploy/swarm/stack.yml`, derived from the Compose project:

- one `graph-os` service; the engine runs **co-located in the graph-os task** as
  a supervised child (`child` placement, as in Compose) — Swarm has no pod
  sidecar;
- `replicas: 1` and `update_config.order: stop-first` (also for rollback), so
  the old task exits and releases the engine store's single-writer lock before
  the new task starts;
- a placement constraint `node.labels.graphos.data == true`: the data volume is
  node-local, so exactly one node carries that label;
- an overlay network (`attachable`) for a TLS proxy and fleet connectors;
- ports published in `mode: host` on the pinned node (drop them when a proxy on
  the overlay terminates TLS);
- the engine HMAC secret as a Swarm secret (`graphos_service_auth_secret`,
  external), exported into the environment by the entrypoint — the runtime
  reads secret values from its environment or its secrets backend, not files.

## Procedure

1. Preflight: Swarm manager quorum (odd number of managers across failure
   domains), advertise/listen addresses, time sync, firewall ports (2377,
   7946 tcp/udp, 4789 udp), overlay CIDRs vs host/VPN ranges, MTU, registry
   trust on every node, CPU baseline of the node that will hold the data.
2. Label the data node: `docker node update --label-add graphos.data=true <node>`.
3. Create the secret once:
   `openssl rand -hex 32 | docker secret create graphos_service_auth_secret -`.
4. Set `GRAPHOS_IMAGE` (immutable digest) and the identity mode (as in Compose:
   `local` by default; `none` needs the exact `GRAPHOS_AUTH_NONE_EXPOSE`
   acknowledgement because a task never binds loopback).
5. Validate: `docker stack config -c deploy/swarm/stack.yml` (or
   `docker compose -f deploy/swarm/stack.yml config` where `stack config` is
   unavailable), then inspect the rendered model for unresolved values.
6. `docker stack deploy -c deploy/swarm/stack.yml graph-os`, then hand off to
   `graphos-deployment` for first-boot verification.

## Limits (state them to the operator)

- **No NetworkPolicy.** Isolation is only the overlay network plus host
  firewalling; any container attached to the overlay can reach graph-os and,
  if exposed, the engine listener. Keep the overlay dedicated, publish nothing
  you do not need, and rely on graph-os/engine authentication, never on network
  reachability.
- **No External Secrets.** Swarm secrets are immutable: rotation means creating
  a new secret, updating the service to use it (a stop-first restart), then
  removing the old one. Values from an external store (OpenBao) are copied into
  Swarm secrets by an operator step — there is no controller that keeps them in
  sync; record the rotation owner.
- **Single node for the data.** The store never moves on its own: node loss is
  a restore to another labelled node, not a failover. Plan the backup/restore
  drill accordingly.
- **Routing mesh.** Ports published in the default `ingress` mode answer on every
  node; the stack uses `mode: host` on purpose.
- **No shared-engine topology.** Swarm runs the unified topology only; stateless
  graph-os replicas against a shared engine need Kubernetes.

## Moving off Swarm

To Compose (one host) or Kubernetes (several hosts): follow
[orchestrator-migration-cutover.md](orchestrator-migration-cutover.md).
