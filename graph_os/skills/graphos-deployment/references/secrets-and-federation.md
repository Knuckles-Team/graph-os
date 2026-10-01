# Secrets and federation sources

## Secrets backend

`SECRETS_BACKEND` selects where runtime secret values live. Configuration only
ever carries references.

| Backend | Use for | Notes |
|---|---|---|
| `engine` | tiny and single-host installs | the engine's encrypted secrets graph; references look like `engine://__secrets__/<name>`; no external service; backed up with the engine store |
| `vault` | production | OpenBao or another Vault-compatible store (`SECRETS_VAULT_URL`, an auth method such as AppRole or Kubernetes auth); references look like `vault://<mount>/<path>#<key>` |

Rules:

- The engine's own bootstrap secrets (`GRAPH_SERVICE_AUTH_SECRET`, the engine
  data key when encryption at rest is on, signer keys where the topology needs
  them) are delivered to the process environment from the runtime Secret or
  env file — never from the secrets graph they protect.
- Write to a Vault-compatible store by patching the one key you own (a KV
  `patch`, not a whole-path `put` that drops sibling keys), then verify the new
  value actually reached the container environment; external-secret
  controllers refresh on an interval, and a hand-edited managed Secret is
  reverted on the next reconcile.
- On Docker Swarm, bootstrap secrets are Swarm secrets (immutable, mounted
  under `/run/secrets/`, exported into the environment by the stack's
  entrypoint). There is no External Secrets controller: rotate by creating a
  new secret, switching the service to it (a stop-first restart) and removing
  the old one; a value that originates in OpenBao is copied by an operator step
  with a recorded owner.
- Prefer workload identity (Kubernetes auth bound to one service account,
  namespace and audience, policy scoped to exactly the paths needed) over a
  static store token. Verify the auth method exists; do not assume it.
- Rotation is a sequence, not a command: write, wait for delivery, restart the
  readers in dependency order (engine before graph-os), verify end to end.
- A `GRAPH_SERVICE_AUTH_SECRET` that graph-os auto-generated on a tiny install
  must be rotated before the install leaves demo mode (the doctor flags it).

## External databases today: the mirror path

The engine is the only durable authority. Today an external database
(Postgres/AGE, Neo4j or FalkorDB) is only reachable as an async **mirror**
(`GRAPH_MIRROR_TARGETS`): the engine pushes its graph into it for interop, BI
or disaster recovery; Graph OS does not read queries back from it. Select a
mirror target per profile (`single-node-prod` and `enterprise` default to
`age`); credentials are secret references, never plaintext. Kafka is not a
mirror target: it stays the task-queue/event backbone
(`TASK_QUEUE_BACKEND=kafka`, `KAFKA_BOOTSTRAP_SERVERS`) where the profile
selects it.

## Not available yet

Registering an external database (Postgres, a graph database, a SQL query
engine, a lakehouse catalog, a triple store) as a **tenant-scoped federation
source** — the engine plans queries across it and records provenance, rather
than Graph OS copying its graph into a mirror:

- register/list/probe/share operations through the control plane, with
  credentials as secret references;
- each internal SQL/HTTP host allowed in the engine's federation allowlist
  (`EPISTEMIC_GRAPH_FEDERATION_ALLOW`), which refuses internal addresses by
  default to prevent server-side request forgery;
- the federation API operations that expose register/list/probe/share over
  MCP/API/A2A.

Once federation sources ship, the mirror path above stops taking new targets:
an install that still has one keeps it until its federation source replaces
it, then removes the mirror target and its credentials in the same change.
