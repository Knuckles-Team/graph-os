# Identity and access

## Availability

| Capability | Available from |
|---|---|
| Identity modes `none`/`local`/`external`, local issuer, first-run setup code, `graph-os-identity` CLI, break-glass, admin UI | train 7 (identity) |
| Eunomia enforced on the served graph-os MCP/API surface (discovery, load and call filtering) | Wave C (MCP intent surface) |
| graph-os capacity scopes, `fleet:events` for graph-os, exact `mcp:discover`/`mcp:delegate` fleet scopes | Wave B |
| Principal-scoped lease-kind allowlist in the engine | train 6 (finance-ops) |

Before train 7, graph-os authenticates with an external OIDC issuer only
(`AUTH_TYPE=jwt`, `AUTH_JWT_ISSUER`, `AUTH_JWT_JWKS_URI`, `AUTH_JWT_AUDIENCE`)
and refuses any non-loopback bind without a token verifier. Follow "Before
train 7" at the end of this file on those releases.

## One authorization path, three ways in

The mode only changes how graph-os learns who the caller is. After that there
is one path in every mode:

```
authenticator (per mode) → identity store → ONE stable principal id + scopes
  → graph-os local issuer mints a short-lived token (sub = principal)
  → session → engine envelope → engine scope check / RBAC → executor
```

Principal ids are opaque and stable (`usr:<uuid>`, `usr:bootstrap`); an external
identity is a *link* to a principal, so changing modes never changes data
ownership. The identity store lives in the engine; graph-os serves the web
flows.

## Modes

The mode is durable engine state. `GRAPHOS_AUTH_MODE` seeds it on first boot
only; the doctor reports a disagreement and the stored mode wins.

### `none` — demo, loopback only

The authenticator always answers "the bootstrap administrator". It is not
"auth off": the engine still authenticates graph-os, and the bootstrap
principal is a normal administrator that is **not** in any approver group, so
two-person approvals stay pending and live financial orders are refused.

Guard rails (do not work around them):

- graph-os and the web UI bind loopback only; a non-loopback bind is refused
  unless `GRAPHOS_AUTH_NONE_EXPOSE` is exactly
  `I-UNDERSTAND-ANYONE-WHO-CAN-REACH-THIS-PORT-IS-ADMIN` (a container always
  needs it — then publish on loopback only);
- `single-node-prod` and `enterprise` refuse `none` without the same
  acknowledgement, and the doctor reports it red;
- every request's Host must be `localhost`, `127.0.0.1` or `[::1]` (plus
  `GRAPHOS_AUTH_NONE_HOSTNAME` when set); state-changing requests need a
  same-origin `Origin` (DNS-rebinding and CSRF defence);
- a red banner, a `[DEMO]` title, `mode: "none"` from `/auth/session`, an MCP
  initialize notice and a startup warning mark the install unsecured.

### `local` — built-in users

Registration is administrator-only by default. Every fresh instance forces
creation of the first administrator through `/auth/setup` with a one-time setup
code (`GRAPHOS_SETUP_CODE`, or generated and written to the graph-os log). The
form is not first-come-first-served. Passwords follow NIST 800-63B; argon2id
hashes are verified inside the engine. MFA (TOTP, WebAuthn, recovery codes) is
optional for all users and administrators can require it per group: require it
for the administrators, both approver groups and the break-glass account. E-mail
is optional: without an SMTP adapter,
recovery is administrator reset plus recovery codes.

### `external` — an identity provider for people

One or more OIDC providers (auth code + PKCE), SAML 2.0 (native service
provider), LDAP/AD (LDAPS or StartTLS only) and a SCIM 2.0 server. IdP claims
feed ordered mapping rules; memberships from an IdP are recomputed on every
login and sync. JIT provisioning per IdP (`create`, `link_by_verified_email` —
off by default, `deny`). Upstream human tokens are verified by graph-os and
exchanged, never forwarded as the principal token. Service accounts may keep
presenting IdP client-credentials tokens; the engine trusts the IdP as a
second issuer for service principals only.

**Break-glass.** Run `external` with `local_fallback=break-glass`: local
credentials remain for administrators only, so an IdP outage never locks every
operator out. Give the break-glass administrator MFA and test it with the IdP
unreachable.

## Transitions

| From → to | Precondition | Command |
|---|---|---|
| none → local | an administrator with a local credential | `graph-os-identity claim --username <name>` |
| none/local → external | an enabled IdP and an administrator linked to it | `graph-os-identity link-claim` (one-time 10-minute code, then sign in with the IdP), then `graph-os-identity transition --to external --local-fallback break-glass` |
| external → local | every administrator has a local credential | `graph-os-identity transition --to local` |
| any → none | loopback bind + the exact acknowledgement + an identity-admin session | `graph-os-identity transition --to none --ack <text>` |

Every transition rotates the issuer signing key and revokes all sessions. No
transition deletes an account, credential, link, grant or owned datum. Other
commands: `reset-admin --principal <id>` (single-use reset token, printed
once), `rotate [--revoke]`. Leaving `none` for the first time: rotate an
auto-generated `GRAPH_SERVICE_AUTH_SECRET` too (the doctor flags it), then
restart graph-os and the engine. Every transition needs operator approval.

## Eunomia per mode

| Mode | `EUNOMIA_TYPE` default | When on and the policy service fails |
|---|---|---|
| `none` | `none` (off): visibility = the bootstrap administrator's scopes | fail closed |
| `local` | `embedded` with the shipped policy ("allow what your scopes allow; deny destructive fleet operations to non-administrators") | fail closed |
| `external` | `embedded` or `remote` (`EUNOMIA_REMOTE_URL`) | fail closed |

Eunomia only narrows authority, never grants it. With it off, visibility is
still scope-filtered, never unfiltered; the doctor warns "per-item policy off".
Provide a custom document with `EUNOMIA_POLICY_FILE`.

## Scopes

The scopes a verified identity can carry into a session are exactly the scopes
the engine registers (its contract's scope list; the orchestration library's
session allowlist is generated from it — **from train 7**). A role the registry
does not list is dropped at the session boundary: adding a scope needs an
engine release, not a configuration edit. Scope classes are enforced by the
identity store:

- **user** (`kg:read` < `kg:write` < `kg:admin`, hierarchical) and **domain**
  scopes (e.g. `finance:alerts`, `finance:track`) — assignable to people;
- **service-only** (`capacity:*`, `lease:*`, `broker:*`, `timeseries:write`,
  `compute:*`, `security:check`, `fleet:events`, `admin:cluster-read` for
  placement resolution, `identity:authenticate` for the graph-os identity
  broker, telemetry-write) — refused on any human;
- **approver** (`rbac:approve-elevation`, `finance:approve-live-order`) — only
  through the built-in groups `elevation-approvers` / `live-order-approvers`,
  human, direct membership; never a service account or API key; with no
  members every such request is refused;
- **admin** (`kg:admin`, `webui:admin`, `identity:admin`, `identity:read`) —
  human only. `webui:admin` opens UI admin surfaces and grants nothing
  graph-side; `kg:admin` implies neither the identity scopes nor
  `graph:admin` (the exact graph-lifecycle scope for create/delete/clear graph).

**graph-os's own service identity** holds exactly `capacity:throttle`,
`capacity:admin`, `capacity:lease`, `capacity:read`, `node:read`, `node:write`,
`fleet:events`, `identity:authenticate` (train 7), and `lease:read`/`lease:write` restricted by the engine's
principal-scoped **lease-kind allowlist** to the kinds graph-os writes
(`EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON`; any other kind is refused).
Never `kg:admin`, never an approver scope. A telemetry collector gets its own
identity scoped to telemetry-write for one tenant.

Fleet access is granted separately with the exact scopes `mcp:discover` and
`mcp:delegate`; no administrative scope implies them (**from Wave B**).

Remote Eunomia (`EUNOMIA_TYPE=remote`) requires HTTPS for any policy service
off loopback; plan its certificate with the rest of the PKI.

Domain users never hold infrastructure scopes: graph-os executes on their
behalf after checking the caller's domain scope and read authority over the
subject (the confused-deputy rule).

## Before train 7 (external OIDC only)

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

## Symptom → cause

| Symptom | Cause | Fix |
|---|---|---|
| UI loads, graph shows 0 nodes, no tools, panels 503, data provably present | the user holds only a UI role, no `kg:*` scope | grant the graph scope, sign in again |
| a granted permission has no effect | stale token minted before the grant | sign out and in |
| setup page cannot be completed | wrong or missing setup code | read `GRAPHOS_SETUP_CODE` or the log line; never bypass the gate |
| startup refused: non-loopback bind in `none` | missing acknowledgement | use `local`, or set the exact acknowledgement and publish on loopback |
| every approval stays pending | the approver group is empty, or the only member is the requester | add a second human deliberately (with MFA required for the group) |
| discovery empty with `POLICY_UNAVAILABLE` | Eunomia on, policy service unreachable | restore the policy service; do not turn Eunomia off |

## Cutover acceptance (train 7)

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
