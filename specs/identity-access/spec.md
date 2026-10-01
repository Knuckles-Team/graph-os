# GRAPHOS-IDENTITY-ACCESS — identity, authorization, and access operations

## State legend

| State | Meaning |
|---|---|
| PROPOSED | Intent needs a design or product decision. |
| READY FOR IMPLEMENTATION | Requirements, owner boundary, contract and tests are specified; work may start. |
| BUILDING | An implementation branch exists; no mainline claim follows. |
| LANDED | The exact implementation commit is on this repository's default branch. |
| ACCEPTED | Required contract, security, end-to-end and quality evidence is recorded for the landed commit. |

The state applies to this whole GraphOS capability. Existing source is **not** proof of LANDED or ACCEPTED here. `evidence.md` records exact commits, jobs, and remaining gaps. Spec publication itself earns no delivery credit.

## Outcome and scope

An operator can begin on a loopback demo, claim a durable local administrator, add external identity providers, manage users and service credentials, and request access through one GraphOS API. Browser, REST, MCP, A2A, and CLI callers enter the same principal, scope, RBAC, and engine authorization path. A remote contributor can build and test the feature with local fixtures and mock providers, without an existing private deployment.

GraphOS owns authentication ceremonies, local issuer, session and API composition, identity/access REST and MCP operations, serving policy, CLI, and diagnostics. The engine owns durable identity records, RBAC, grant/lease decisions, transactionality, and audit authority. Agent orchestration consumes a verified principal and cannot grant its own scopes. The browser app displays and invokes GraphOS APIs; it does not infer roles. No second GraphOS user database is permitted.

## User stories and acceptance

1. **Bootstrap safely (P0).** A fresh tiny install on loopback can act as `usr:bootstrap` via a real signed engine session. A production profile requires local setup unless an explicit exposure acknowledgment is supplied. A network or DNS-rebinding attempt without that acknowledgment fails before a token is minted.
2. **Move between modes without losing ownership (P0).** An administrator can claim, link, and transition among `none`, `local`, and `external`. Principal IDs, owned objects, roles, and grants survive a reversible round trip. Every transition rotates credentials and revokes prior browser sessions as specified below.
3. **Administer identities and access (P0).** A fresh-MFA console administrator can manage users, sessions, keys, roles, groups, providers, and policies; review audit events; request/approve/deny/revoke elevation; and explain effective access. A delegated agent, stale MFA session, or service token cannot perform a console-only mutation.
4. **Use local or external login (P1).** Local password, TOTP, WebAuthn, recovery code, OIDC, LDAP, SCIM, and optional SAML-broker flows produce the same stable principal and narrowed effective scopes. A revoked session, key, or group membership loses access on the next relevant check.
5. **Develop in isolation (P0).** A contributor runs contract tests with a local engine fixture and mock IdP/LDAP/SMTP adapters. No public PR gate needs a pre-existing host service, private realm, inventory, or secret.

## Functional requirements

| ID | Requirement and independently observable result | Source IDs |
|---|---|---|
| IA-01 | Read durable `auth.mode ∈ {none,local,external}` from the engine singleton; use configuration only to seed first boot; mode transitions use an epoch compare-and-swap and typed conflict. | GRAPHOS-IDENTITY-R003, GRAPHOS-IDENTITY-R004, GRAPHOS-IDENTITY-R013 |
| IA-02 | Resolve one opaque, stable principal ID before minting a short-lived RS256 local-issuer token. `sub` equals that ID. Publish discovery and JWKS; store the signing key through a secret reference; rotate with bounded overlap. Never forward an upstream human token as the engine principal token. | GRAPHOS-IDENTITY-R003 |
| IA-03 | In `none`, resolve `usr:bootstrap` through the normal RBAC/scope path, refuse non-loopback binds without an exact explicit acknowledgment, validate Host and same-origin Origin for cookie mutations, and emit an unsecured mode notice in session/status/MCP initialize/doctor. | GRAPHOS-IDENTITY-R004 |
| IA-04 | Implement local registration policy, login/logout, password change and reset, opaque server-side session, CSRF token, session fixation rotation, throttling and uniform public errors. Session revocation takes effect by the next request. | GRAPHOS-IDENTITY-R005 |
| IA-05 | Support TOTP with replay guard, WebAuthn with origin/RP binding and signature counter policy, one-use recovery codes, and group-configured MFA enrollment. Enrollment is optional by default; privileged console actions still require fresh MFA. | GRAPHOS-IDENTITY-R006 |
| IA-06 | Expose identity operations for self, users, sessions, keys, service accounts, roles, groups, providers, mapping dry-run, policy, mode, issuer and audit. Mutations require exact `identity:admin`, operator-console origin and fresh MFA. `identity:provision` is a separate SCIM-only scope. | GRAPHOS-IDENTITY-R007, GRAPHOS-IDENTITY-R018 |
| IA-07 | Replace static MCP tokens with expiring service-account/API keys. At every use, effective key scopes are intersected with current owner scopes; revoked/expired keys fail immediately. Keys cannot carry approver scopes. | GRAPHOS-IDENTITY-R008, GRAPHOS-IDENTITY-R023 |
| IA-08 | Support multiple OIDC providers with PKCE, state, nonce, verified issuer/audience, deterministic ordered mapping, JIT policy, explicit link-claim, and upstream logout hint. Verified-email linking defaults off. Removing an upstream group removes its sourced role at next login/sync. | GRAPHOS-IDENTITY-R009 |
| IA-09 | LDAP binds use TLS, escaped filters and bounded group sync including nested AD groups; disabled accounts deprovision. SCIM `/scim/v2` uses a provider-bound service account; `active=false` revokes credentials but preserves owned data. SAML may be brokered through an OIDC provider until a reviewed native SP contract exists. | GRAPHOS-IDENTITY-R010, GRAPHOS-IDENTITY-R011, GRAPHOS-IDENTITY-R012 |
| IA-10 | `graph-os identity` CLI supports claim, link-claim, transition, reset-admin and rotate. Doctor reports exposed `none`, automatic service secrets, missing TLS and unavailable optional adapters with actionable codes. | GRAPHOS-IDENTITY-R013, GRAPHOS-IDENTITY-R015 |
| IA-11 | Run a shared decision table `(principal,scope,action,resource) → (allow/deny,reason)` in all three modes. Same principal and grants produce identical claims digest and engine RBAC outcome, including after a full mode round trip. | GRAPHOS-IDENTITY-R014 |
| IA-12 | Expose access request/list/approve/deny/revoke, approval list/grant/deny, lease and `check`/`explain_policy` operations through one service backed by engine authority. No GraphOS-only approval or lease record. | GRAPHOS-IDENTITY-R001, GRAPHOS-IDENTITY-R019 |
| IA-13 | Register domain scopes by exact name and class, including `finance:read`, `fleet:read/control`, `loops:read/control`, `ops:read/admin`; consume the generated engine scope registry. `kg:admin`, `admin` and local stdio do not imply fleet or MCP scopes. Unknown tools and missing authority fail closed. | GRAPHOS-IDENTITY-R002, GRAPHOS-IDENTITY-R020, GRAPHOS-IDENTITY-R022 |
| IA-14 | Depend on identity ports/DTOs from serving composition; inject implementations into MCP, gateway and web host. No circular import or second issuer/session implementation is allowed. | GRAPHOS-IDENTITY-R021 |
| IA-15 | Optional SMTP sends reset, verification and invite messages through a configured adapter. If absent, hide email-only affordances and retain admin-reset/recovery-code paths. | GRAPHOS-IDENTITY-R016 |
| IA-16 | Every GraphOS engine client, including bootstrap, REST, MCP, A2A, CLI and background service calls, supplies a verified local-issuer or permitted service token before the engine removes its authentication opt-out. A missing, invalid or denied token must never trigger an unsigned retry or compatibility fallback. | GRAPHOS-IDENTITY-R017 |

## Cross-repository contract, fully stated here

The engine accepts an authenticated request with tenant, audience, `sub`, `scope` and role claims; it verifies issuer/JWKS and computes RBAC, CheckAccess, elevation, lease, identity writes and audit atomically. GraphOS must treat a missing method, stale capability registry, or unavailable engine as `UNAVAILABLE` or a typed denial and must not locally approve. GraphOS must prove signed token carriage on every client path before the engine removes its authentication opt-out; afterward, the engine refuses unsigned envelopes. The engine's typed identity methods return redacted records and decision reason codes; password/hash/secret columns never cross the API. Scope class rules are: `user` and `domain` may be assigned to humans; `service-only` may only reach service accounts; `approver` only reaches direct human members of designated approver groups and never API keys; `admin` reaches humans subject to configured group MFA rules. A role change replaces the complete engine RBAC role projection in the same transaction.

The agent layer receives the verified principal, narrowed scopes, tenant and delegation chain, and must not mint a stronger identity. The browser UI uses `/auth/session` and the REST operation results as its only authority. These statements are part of this spec and remain actionable if another repository's spec is unavailable.

## Non-goals and unresolved decisions

GraphOS does not store durable identity tables, evaluate final graph RBAC, own source-specific connector credentials, or rewrite object owners during authentication changes. Native SAML XML processing is deferred; an OIDC broker is the initial supported path. A concrete SMTP service is optional. Existing row labels are historical intake labels, not current verification.

## Completion rule

Mark LANDED only with an exact default-branch commit containing all GraphOS behavior in scope. Mark ACCEPTED only when the contract, negative security, browser/MCP/CLI, three-mode oracle, quality, and release tests in `test-spec.md` pass on that commit and the evidence is recorded. A partial implementation must retain an explicit gap and cannot advertise its operation as complete.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
