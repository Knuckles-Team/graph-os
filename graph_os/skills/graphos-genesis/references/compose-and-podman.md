# Docker Compose and Podman (one host)

Use for a laptop, a workstation or one durable server. The graph-os source tree
ships the project at `deploy/compose/`:

| File | Purpose |
|---|---|
| `compose.yaml` | one `graph-os` service: MCP + REST/A2A on 8000, web UI on 8080, engine as a supervised child, one data volume, ports published on `127.0.0.1` |
| `.env.example` | interpolation values: `GRAPHOS_IMAGE` (immutable reference), identity mode, Eunomia type, telemetry targets |
| `graph-os.env.example` | secret values read at runtime: `GRAPH_SERVICE_AUTH_SECRET`, `GRAPHOS_SETUP_CODE` |

Copy both examples, fill them, never commit the copies (the directory's
`.gitignore` covers them).

## Procedure

1. Confirm Docker Compose v2.24+ (optional `env_file` entries) or a Podman
   with `podman compose`, and enough disk for the engine store.
2. Choose the image: a unified graph-os image at a digest (see
   [runtime-topology.md](runtime-topology.md), "Images").
3. Choose the identity mode (applied by `graphos-deployment`):
   - `local` (default): the first administrator is created in the web UI at
     `/auth/setup` with a one-time setup code from `GRAPHOS_SETUP_CODE` or the
     graph-os log;
   - `none` (demo): set `GRAPHOS_AUTH_MODE=none`, `EUNOMIA_TYPE=none` and the
     exact acknowledgement `GRAPHOS_AUTH_NONE_EXPOSE=I-UNDERSTAND-ANYONE-WHO-CAN-REACH-THIS-PORT-IS-ADMIN`
     in `.env` — required because the process inside a container never binds
     loopback. Keep the `127.0.0.1` port publishing and browse
     `http://localhost:8080` only; the none-mode Host/Origin guard refuses
     other names;
   - `external`: start in `local`, then add an IdP (see `graphos-deployment`).
   Identity modes are **available from train 7 (identity)**. Before it, a
   container bind is non-loopback, so graph-os refuses to serve without a real
   token verifier (`AUTH_TYPE=jwt` against an existing OIDC issuer); for a
   zero-IdP evaluation on an older release, run graph-os directly on the host
   in the `tiny` profile instead ([bare-metal.md](bare-metal.md)).
4. Validate offline, then inspect the rendered model for unresolved values and
   secret leakage:

   ```bash
   docker compose config --quiet
   docker compose config | grep -iE 'secret|token|password'   # expect names only
   ```

5. Capture the prior image digest and volume snapshot, then `docker compose up -d`.
6. Hand off to `graphos-deployment` for first-boot verification.

## Rules

- Never scale the `graph-os` service: the engine store has one writer.
- Keep secrets in `graph-os.env`, a Docker/Podman secret, or the engine secrets
  graph — never in `compose.yaml` or `.env`.
- Publishing on a non-loopback address is an exposure decision: use `local` or
  `external`, and terminate TLS in a reverse proxy in front of both ports.
- Back up the `graph-os-data` volume with the service stopped or through
  graph-os's guarded backup operation; test a restore.
- Connectors run as additional services or separate hosts; graph-os reaches
  them through the fleet multiplexer configuration, not by sharing its volume.

## Podman

- `podman compose` runs the same file (it delegates to a Compose provider).
  Podman Compose compatibility is not proof of Docker parity: test health,
  secrets, networking, volume labels and restart semantics on the version in
  use.
- Rootless Podman suits development: published ports below 1024 need
  `net.ipv4.ip_unprivileged_port_start` or a proxy; volumes need SELinux
  relabelling (`:Z`) on enforcing hosts; use `loginctl enable-linger` for
  services that must outlive the login session.
- For a durable single host, prefer rootful Podman with Quadlet units
  (`.container` + `.volume`) generated from the same model, so systemd owns
  restart and boot ordering. Validate the units and prove reboot persistence.

## Several hosts

For several Docker hosts use the Swarm stack derived from this project
([docker-swarm.md](docker-swarm.md)), or Kubernetes when NetworkPolicy,
External Secrets or a shared engine are required.

## Gates

- offline render validation, no floating tags in production, no plaintext
  credentials;
- healthcheck and dependency-failure behaviour; disk-pressure behaviour;
- restart and reboot persistence of the engine store;
- external route (if any) and internal isolation;
- backup and restore; idempotent second `up -d`; rollback to the prior exact
  image and configuration.
