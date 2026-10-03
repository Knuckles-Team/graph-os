# Test contract — GRAPHOS-IDENTITY-ACCESS

Delivery state: READY FOR IMPLEMENTATION. Test result: **NOT RUN for this spec**. No pass is inferred from prior source snapshots.

## Portable fixture

Use an ephemeral engine process or in-memory contract fixture implementing the public identity/access methods, a generated local signing key, deterministic clock, mock OIDC issuer/JWKS, mock LDAP directory, SCIM client, mock SMTP port and browser test server. The ordinary PR suite must provision its own dependencies and data; it must not depend on an operator's live identity provider, host inventory, secret backend, network address or sibling checkout. A missing optional live adapter is a recorded skip in a separate deployment profile, never a reason to skip the local contract fixture. Secrets remain fixture-only.

| Test | Requirements | Level and fixture | Positive result | Negative / boundary result |
|---|---|---|---|---|
| IT-01 | IA-01, IA-02 | Engine contract + local issuer | First boot seeds mode once; discovery/JWKS verify a token whose `sub` is stable; CAS increments epoch. | Competing epoch conflicts; wrong `kid`, audience, tenant and expired token fail; configuration drift does not overwrite stored mode. |
| IT-02 | IA-03 | Served loopback HTTP/MCP | `none` resolves `usr:bootstrap`, reaches engine RBAC, displays mode and warning in session/status/MCP initialize. | Non-loopback without exact acknowledgment refuses startup; DNS-rebinding Host and missing/cross-origin Origin on cookie mutation reject before token minting; approver/live-order requests deny. |
| IT-03 | IA-04 | Browser + local account fixture | Register under policy, log in, rotate session on login/privilege change, change password, reset once, log out; next request sees revocation. | Unknown username and wrong password give same public response; throttling applies; CSRF missing/replay, reused reset token, stale session, fixation ID all fail. |
| IT-04 | IA-05 | TOTP/WebAuthn ceremony fixtures | Valid TOTP, passkey and recovery code authenticate; configured group rule requires enrollment; action step-up succeeds within freshness window. | Replayed TOTP step/code, wrong RP/origin/challenge, stale MFA, non-enrolled required-group member and bad sign counter fail; optional-default user can sign in without MFA. |
| IT-05 | IA-06 | REST/MCP operation contract | Admin console can manage a user, role, session, key, provider, policy and audit; self sees own redacted record; dry-run names resulting roles. | Delegated or service session, absent exact `identity:admin`, stale MFA, wrong tenant and MCP admin mutation deny; secret/hash fields never appear. |
| IT-06 | IA-07 | API-key + owner-scope fixture | Key with subset of current owner scopes works; refresh is in process and one session survives key rotation as specified. | Revoked/expired key, owner scope removal, approver scope, static token, missing key backend and replayed refresh family deny on next use. |
| IT-07 | IA-08 | Two mock OIDC issuers | PKCE, state/nonce and subject link produce stable principal; group removal removes sourced role; id_token_hint sent on logout. | Wrong issuer/audience/signature/nonce, callback replay, unverified-email auto-link, duplicate link and privileged mapping without fresh admin MFA deny. |
| IT-08 | IA-09 | Mock TLS LDAP, SCIM client, OIDC SAML broker | Escaped query returns bounded nested memberships; SCIM PATCH deprovisions and revokes session/key while preserving owned data; brokered SAML yields verified OIDC identity. | Plain LDAP, filter injection, disabled AD account, SCIM token for another provider, malformed PATCH, SCIM restore without policy, and raw unverified SAML assertion deny. |
| IT-09 | IA-10, IA-15 | CLI/doctor and fake SMTP | Claim/link/transition/rotate produce typed result; doctor reports exposure and optional SMTP; configured SMTP sends a one-use reset. | Token not printed twice or logged; absent SMTP hides email-only option; exposed demo and automatic service secret cause diagnostic failure. |
| IT-10 | IA-11 | Shared YAML decision table on `none`, `local`, `external` with mock OIDC + LDAP | For each `(principal,scope,action,resource)`, effective claims digest, allow/deny and reason code match; `none→local→external→local→none→local` leaves principals, owners and grants unchanged. | Deliberate role/scope divergence fails oracle; stale mode token fails after transition; concurrent flip conflicts. |
| IT-11 | IA-12 | Engine access contract + REST/MCP | Request/approve/deny/revoke, approval list, lease, check and explain return engine decision and audit receipt; route and MCP twin agree. | Self approval, delegated approval, expired lease, unavailable engine, missing exact scope and graph-only privilege shortcut deny; no local fallback receipt. |
| IT-12 | IA-13 | Registry and dispatch property tests | Every advertised operation has an exact registered scope/class; domain registry export matches engine source; fleet operation passes with exact fleet grant. | `admin`, `kg:admin`, stdio transport, unknown tool and omitted registry entry never grant fleet/MCP access. |
| IT-13 | IA-14 | Import graph + composition test | One server/issuer/session implementation is constructed and injected through ports; REST/MCP/web host share it. | Import cycle, orphan implementation, duplicate authority or direct web-host import of MCP implementation fails. |
| IT-14 | IA-16 | Served engine verifier plus each GraphOS client entry point | Bootstrap, REST, MCP, A2A, CLI and background service requests carry an accepted signed local-issuer or permitted service token, with expected allow and deny decisions. | Missing/expired/wrong-audience token and explicit engine denial produce a typed error; no unsigned retry, fallback credential or auth-opt-out branch is invoked. An old client fails closed after engine opt-out removal. |

## Acceptance evidence and release checks

For each test, `evidence.md` must link a reproducible job or command with exact commit, fixture revision, result and any justified skip. The public PR gate runs the full portable suite on a clean checkout. A separate served deployment qualification may exercise a real provider; it cannot substitute for portable contract and negative tests. Browser tests must prove rendered warnings, login/MFA, key revocation and admin restriction; API-only tests are insufficient for the browser story.

Run repository checks from a clean checkout with `uv sync --extra test`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os`, `uvx --from pre-commit==4.6.0 pre-commit run --all-files`, and the release workflow's build/lock checks. The scanner workflow provisions pinned CCCC, KISS, Dupehound and jscpd. Use the repository's configured thresholds and differential baseline; do not change them to fit this implementation. The current hook configuration names `complexity-staged`, `kiss-staged`, `dupehound-changed`, `complexity-census`, `kiss-census`, `jscpd-differential`, `jscpd-census`, and `scanner-versions`; the release workflow pins tool versions. Apply KISS with ports and one composition root, preserve the orphan-module gate, and reject duplicate issuer, policy and RBAC implementations. Record unavailable tool/threshold as a gap, never as a pass.

No pass on an unrelated branch, unmerged patch or earlier commit qualifies the exact landed commit. Acceptance requires IT-01 through IT-14, scanner/quality/release jobs, portable fresh-checkout proof, and resolved design decisions. A row can remain partial even if this umbrella spec is otherwise accepted.

## Strict producer regression slice

Focused source tests cover malformed/missing authority fields, immutable copied
resolution/context sets, direct-only delegation, exact scope narrowing and
source-expiry-bounded local claim preparation. Browser tests cover duplicate or
invalid cookies, exact configured Origin, mutation CSRF, cookie lifetime,
private credential digests and request/session-instance substitution, including
mutation across an await. Source fixtures do not qualify the EG producer, local
signing keys, session persistence, installed generated bindings or mounted
HTTP/MCP/WebUI serving. Those acceptance requirements remain outstanding.
