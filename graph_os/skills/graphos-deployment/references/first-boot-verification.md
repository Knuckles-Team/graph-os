# First-boot verification

Run every applicable step in order and record evidence (redacted). A green
health endpoint proves the process is up — nothing more. The deployment is
`verified` only when a **person can sign in through a browser** and reach the
graph, and an agent can reach the same backend over MCP.

## 1. Process and engine

- [ ] `GET /health` and `GET /health/ready` on the API port return 200 with an
      allowed Host header; readiness reports the engine reachable.
- [ ] Exactly one writer: one graph-os pod/container; the engine store lock is
      held by that unit only.
- [ ] `agent-utilities-doctor` (and `setup-config doctor --profile <profile>`)
      has no failing in-scope check. Note every WARN: `none` mode exposed,
      auto-generated engine secret, Eunomia off, default policies.
- [ ] The persist directory is the durable volume (not ephemeral storage).

## 2. Identity (train 7 and later)

- [ ] `GET /auth/session` reports the expected `mode`.
- [ ] `none`: the banner and `[DEMO]` title show; a request with a non-local
      `Host` is refused; the bind is loopback (or the acknowledgement is set
      and publishing is loopback-only).
- [ ] `local`/`external` fresh install: `/auth/setup` refuses a wrong setup
      code, accepts the right one once, and refuses reuse. The first
      administrator exists; registration policy is `admin_only`.
- [ ] `/.well-known/openid-configuration` and `/.well-known/jwks` serve the
      local issuer; the engine accepts its tokens.
- [ ] `external`: sign in through each enabled IdP; sign out sends the IdP an
      `id_token_hint`; the break-glass administrator signs in with the IdP
      unreachable.

## 3. Browser sign-in path (every mode except `none`)

Drive a real browser session (a headless browser is fine) — never only a
service token:

- [ ] open the web UI hostname, follow every redirect, complete the login form
      (and MFA where required);
- [ ] land on the dashboard with a session cookie (`__Host-graphos_session`
      from train 7);
- [ ] a graph-backed panel shows data (a non-empty graph for a seeded install,
      or a successful empty query) — not 0 nodes with 503s;
- [ ] the fleet/tools panel lists tools the user is entitled to;
- [ ] sign out, and confirm the session no longer works.

Before train 7 the same probe runs against the external IdP's login page.

## 4. MCP and API control plane

- [ ] An MCP client completes `initialize` and `tools/list` over the API
      hostname with a real credential; the intent verbs are listed.
- [ ] `find_tools` returns fleet items for a caller holding `mcp:discover`;
      `load_tools` + one read-only call succeeds for a caller holding
      `mcp:delegate`; a caller without them is refused.
- [ ] The same read through the HTTP API returns the same result (surfaces
      agree).
- [ ] With Eunomia on: an item the policy denies is absent from discovery and
      its call is refused; stopping the policy service makes discovery fail
      closed (`POLICY_UNAVAILABLE`), then restore it.
- [ ] One read-only delegated run on the configured model calls an allow-listed
      tool and records model, tool and graph spans in one trace.

## 5. Service identities and policy

- [ ] graph-os's own token carries exactly its service scopes and **neither**
      `kg:admin` **nor** an approver scope.
- [ ] Lease-kind allowlist (train 6+): an allowed kind succeeds, another kind
      is refused for graph-os.
- [ ] Approver groups contain only the intended people; a request by their
      only member stays pending.

## 6. Secrets, persistence, telemetry

- [ ] Every secret setting resolves from its reference; no value appears in
      config, logs or traces.
- [ ] Restart the unit: the engine reopens the store, data is intact, sessions
      behave per policy.
- [ ] A backup was taken and a restore was tested in isolation.
- [ ] Traces arrive at the OTLP endpoint; `/metrics` includes engine series;
      RUM samples arrive when an endpoint is configured.

## 7. Record

Profile, mode, image digests, chart/Compose revision, each step's evidence, open
WARNs with owners, and the rollback artifact.
