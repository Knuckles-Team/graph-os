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
- Prefer workload identity (Kubernetes auth bound to one service account,
  namespace and audience, policy scoped to exactly the paths needed) over a
  static store token. Verify the auth method exists; do not assume it.
- Rotation is a sequence, not a command: write, wait for delivery, restart the
  readers in dependency order (engine before graph-os), verify end to end.
- A `GRAPH_SERVICE_AUTH_SECRET` that graph-os auto-generated on a tiny install
  must be rotated before the install leaves demo mode (the doctor flags it).

## External databases are federation sources, not mirrors

The engine is the only durable authority. External databases — Postgres,
graph databases, SQL query engines, lakehouse catalogs, triple stores — are
registered as **tenant-scoped federation sources**: the engine plans queries
across them and records provenance; Graph OS does not copy its graph into
them.

- Register sources through the federation operations of the control plane
  (register / list / probe / share), with credentials as secret references.
- Allow each internal SQL/HTTP host in the engine's federation allowlist
  (`EPISTEMIC_GRAPH_FEDERATION_ALLOW`): the engine refuses internal addresses
  by default to prevent server-side request forgery.
- Federation of external databases through the engine is **available from
  train 6 (EH-508)**; the federation API operations from **Wave C**.
- The retiring mirror path (`GRAPH_MIRROR_TARGETS` pushing into Postgres/AGE,
  Neo4j or FalkorDB) gets no new targets. An install that still has one keeps
  it until its federation source replaces it, then removes the mirror target
  and its credentials in the same change.
- Kafka is not a federation source: it stays the task-queue/event backbone
  (`TASK_QUEUE_BACKEND=kafka`, `KAFKA_BOOTSTRAP_SERVERS`) where the profile
  selects it.
