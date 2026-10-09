# Identity and access

## Today: external OIDC only

graph-os authenticates with an external OIDC issuer only (`AUTH_TYPE=jwt`,
`AUTH_JWT_ISSUER`, `AUTH_JWT_JWKS_URI`, `AUTH_JWT_AUDIENCE`) and refuses any
non-loopback bind without a token verifier.

- Two OIDC clients: the browser client (auth code + PKCE; the human's identity)
  and the web UI backend's service client (client credentials). A role granted
  to one never flows to the other — always name which one you grant.
- People: `kg:read`/`kg:write`/`kg:admin` for graph access, plus `webui:admin`
  for UI admin surfaces.
- The web UI backend service client: `kg:write` (or `kg:read` for read-only)
  **and** `admin:cluster-read` — every placement resolution needs it and fails
  closed with `ACCESS_DENIED: ... lacks required scope 'admin:cluster-read'`
  without it. Grant only scopes the session allowlist accepts; a role outside it
  is silently dropped.
- After any role change, sign out (`/auth/logout`) and in again: a token minted
  before a grant never carries it.

## Scopes

Scope classes enforced by the identity store:

- **user** (`kg:read` < `kg:write` < `kg:admin`, hierarchical) and **domain**
  scopes (e.g. `finance:alerts`, `finance:track`) — assignable to people;
- **approver** (`rbac:approve-elevation`, `finance:approve-live-order`) — only
  through the built-in groups `elevation-approvers` / `live-order-approvers`,
  human, direct membership; never a service account or API key; with no
  members every such request is refused;
- **admin** (`kg:admin`, `webui:admin`, `identity:admin`, `identity:read`) —
  human only. `webui:admin` opens UI admin surfaces and grants nothing
  graph-side; `kg:admin` implies neither the identity scopes nor
  `graph:admin` (the exact graph-lifecycle scope for create/delete/clear graph).

Fleet access is granted separately with the exact scopes `mcp:discover` (find,
list) and `mcp:delegate` (load, call); no administrative scope implies them —
grant them deliberately to the people and clients that should reach the fleet.

A dedicated telemetry collector identity, scoped to `telemetry-write` for one
tenant, never holds graph scopes. Domain users never hold infrastructure
scopes: graph-os executes on their behalf after checking the caller's domain
scope and read authority over the subject (the confused-deputy rule).

## Symptom → cause

| Symptom | Cause | Fix |
|---|---|---|
| UI loads, graph shows 0 nodes, no tools, panels 503, data provably present | the user holds only a UI role, no `kg:*` scope | grant the graph scope, sign in again |
| a granted permission has no effect | stale token minted before the grant | sign out and in |
| every approval stays pending | the approver group is empty, or the only member is the requester | add a second human deliberately (with MFA required for the group) |

## Not available yet

These capabilities are designed but not served by `main` today. Verify against
the installed release before relying on any of them.

**Identity modes.** A durable, engine-stored identity mode
(`GRAPHOS_AUTH_MODE`: `none`/`local`/`external`) replacing the external-OIDC
path above, with one authorization path regardless of mode:

```
authenticator (per mode) → identity store → ONE stable principal id + scopes
  → graph-os local issuer mints a short-lived token (sub = principal)
  → session → engine envelope → engine scope check / RBAC → executor
```

- `none` — demo, loopback only: the authenticator always answers "the
  bootstrap administrator" (a normal administrator, not in any approver
  group). Guard rails: graph-os and the web UI bind loopback only unless
  `GRAPHOS_AUTH_NONE_EXPOSE` is exactly
  `I-UNDERSTAND-ANYONE-WHO-CAN-REACH-THIS-PORT-IS-ADMIN`; `single-node-prod`
  and `enterprise` refuse `none` without the same acknowledgement; every
  request's Host must be `localhost`/`127.0.0.1`/`[::1]`; a red banner, a
  `[DEMO]` title and a startup warning mark the install unsecured.
- `local` — built-in users: every fresh instance forces creation of the first
  administrator through `/auth/setup` with a one-time setup code
  (`GRAPHOS_SETUP_CODE`, or written to the graph-os log). Passwords follow
  NIST 800-63B; MFA (TOTP, WebAuthn, recovery codes) is optional per user and
  can be required per group.
- `external` — an identity provider for people: one or more OIDC providers,
  SAML 2.0, LDAP/AD and SCIM 2.0, with ordered claim-mapping rules and JIT
  provisioning. Break-glass: `local_fallback=break-glass` keeps local
  credentials for administrators only, so an IdP outage never locks every
  operator out.

Transitions (`graph-os-identity` CLI): `none → local` (`claim`), `none/local →
external` (`link-claim` then `transition --to external`), `external → local`
(`transition --to local`), any `→ none` (`transition --to none --ack <text>`,
needs loopback + the exact acknowledgement + an identity-admin session). Every
transition rotates the issuer signing key, revokes all sessions, and needs
operator approval; none deletes an account, credential, link, grant or owned
datum.

**Request-time Eunomia enforcement.** A policy-gate-backed MCP/API intent
surface exists in code (`graph_os/api/mcp/`) but is not yet mounted into the
served surface. Once it is, `EUNOMIA_TYPE` (`none`/`embedded`/`remote`) selects
per identity mode, fails closed when on and the policy service is
unreachable, and only narrows authority — visibility stays scope-filtered even
with it off.

**graph-os's own service identity** will hold `capacity:throttle`,
`capacity:admin`, `capacity:lease`, `capacity:read`, `node:read`, `node:write`,
`fleet:events`, `identity:authenticate`, and `lease:read`/`lease:write`
restricted by the engine's principal-scoped lease-kind allowlist
(`EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON`; any other kind refused).
None of these scopes or the allowlist exist on `main` today. Never grant it
`kg:admin` or an approver scope once they do.

Related symptoms once served: a setup page that cannot be completed means a
wrong or missing setup code (read `GRAPHOS_SETUP_CODE` or the log line; never
bypass the gate); startup refused for a non-loopback bind in `none` means a
missing acknowledgement; discovery empty with `POLICY_UNAVAILABLE` means
Eunomia is on and the policy service is unreachable — restore it, never turn
Eunomia off to compensate.

## Cutover acceptance

Verify this sequence against the installed release before relying on it; see
[Not available yet](#not-available-yet) above.

Use a fresh store or a backed-up existing one and record the running image
digest before changing identity mode. The mode is durable engine state:
changing `GRAPHOS_AUTH_MODE` after first boot is not a transition. Run the CLI
under an administrator's own authority; it prompts for the password and second
factor. `none → local` uses `graph-os-identity claim --username <name>`;
`local → external` requires an enabled IdP, reviewed mapping dry-run and a
linked administrator before `graph-os-identity transition --to external
--local-fallback break-glass --as <name>`. The signer rotates and sessions are
revoked. If the IdP is unavailable, the local break-glass administrator must
still be able to sign in with MFA.

A browser probe must check the whole path. For local sign-in, submit the
browser's JSON `/auth/login` request from the same origin, complete MFA for the
administrator, and fetch `/auth/session` with the resulting
`__Host-graphos_session` cookie. For external sign-in, begin at
`/auth/oidc/{idp_id}/login`, follow the IdP's PKCE authorization-code form and
callback, then fetch `/auth/session` with that cookie. Compare the returned
principal and **server-resolved** role for ordinary and administrator test
users. Never infer admin authority from a UI label or a service token.

Capture only statuses and synthetic principal identifiers as evidence. Never
log session cookies, bearer tokens, passwords, TOTP seeds, setup codes or reset
tokens. A Keycloak discovery success, health response, and test-namespace pass
are preparatory checks; repeat the browser path on the production URL after
rollout. See the public `docs/identity.md` for the full operator sequence.
