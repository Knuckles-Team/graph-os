# Delivery tasks — GRAPHOS-IDENTITY-ACCESS

State: READY FOR IMPLEMENTATION. Check a task only with an exact commit and test reference in `evidence.md`.

- [ ] **GIA-01 (IA-01/02):** Define engine identity/access DTOs, typed errors, scope classes and test fixture; implement durable mode read/CAS and local issuer key-store adapter. Prove IT-01.
- [ ] **GIA-02 (IA-03/14):** Add identity ports and one serving composition root; route `none` bootstrap requests through the normal signed engine session; enforce bind/Host/Origin policy and warning surfaces. Prove IT-02 and IT-13.
- [ ] **GIA-03 (IA-04):** Implement local account and session ceremonies with hashed server-side IDs, CSRF, rate limits, rotation, revocation and uniform errors. Prove IT-03.
- [ ] **GIA-04 (IA-05):** Add TOTP, WebAuthn, recovery-code and group-MFA policies while preserving action step-up. Prove IT-04.
- [ ] **GIA-05 (IA-06/07):** Build identity self/admin operations and service/API key path; wire REST/MCP/CLI to the same authority. Refuse to advertise unavailable engine-backed methods. Prove IT-05/06.
- [ ] **GIA-06 (IA-12/13):** Build access request, approval, lease, check and explanation service with exact-scope registry and fail-closed fleet/unknown-tool checks. Prove IT-11/12.
  - Partial (GRAPHOS-IDENTITY-R019): `graph_os.access.service`/`graph_os.api.ops.access` registers `access.elevation.request/list/revoke/approve`, `access.leases.list/get`, `access.check` and `access.explain_policy` against the installed EG scope contract. `approvals.list/get/grant/deny` are implemented in the same service module but not yet registered as operations: the installed EG contract does not publish `approvals:read`/`approvals:decide` scopes. Follow-up: register those four operations once EG adds the two scopes.
- [ ] **GIA-07 (IA-08/09/15):** Add mockable OIDC, LDAP, SCIM, brokered SAML and optional SMTP adapters with strict validation, source-tagged memberships and deprovisioning. Prove IT-07/08/09.
- [ ] **GIA-08 (IA-10):** Add `graph-os identity` commands, configuration defaults, doctor diagnostics and public operator guidance within this repository's GitHub Pages source. Prove CLI portions of IT-09.
- [ ] **GIA-09 (IA-11):** Create and run the three-mode decision table and owned-data transition round trip; use it across the engine and GraphOS contract fixture. Prove IT-10.
- [ ] **GIA-09A (IA-16):** Inventory every GraphOS engine client, carry a verified local-issuer or permitted service token through each one, prove allow/deny and no fallback with IT-14, then coordinate removal of the engine authentication opt-out. Record the exact engine and GraphOS commits separately.
- [ ] **GIA-10:** Run all portable tests on the exact candidate commit; run Ruff, Mypy, hooks, CCCC, KISS, Dupehound, jscpd, frozen lock and wheel checks. Fix findings without weakening thresholds; record each job in `evidence.md`.
- [ ] **GIA-11:** Merge the reviewed implementation to default branch; record exact commit and re-run acceptance evidence against that commit. Update delivery and acceptance states individually.

## Rapid-delivery split (2026-10-09)

- [x] **GIA-RD-01:** Split `GRAPHOS-IDENTITY-R004`, `R006`, `R007` into `.1` (typed model +
- [x] **GIA-RD-02:** Split `GRAPHOS-IDENTITY-R008`, `R009`, `R010` into `.1` (typed model +
- [x] **GIA-RD-03:** Split `GRAPHOS-IDENTITY-R011`, `R012` into `.1` (typed model +
  validation + refusal tests under `graph_os/identity/`) and `.2` (remaining behavior,
  stays `SPECIFIED`). Parents are now rollups. See `requirements.md`/`status.json`.

## Source ID coverage

`GRAPHOS-IDENTITY-R001`, `GRAPHOS-IDENTITY-R002`–`GRAPHOS-IDENTITY-R017`, `GRAPHOS-IDENTITY-R018`, `GRAPHOS-IDENTITY-R019`, `GRAPHOS-IDENTITY-R020`, `GRAPHOS-IDENTITY-R021`, `GRAPHOS-IDENTITY-R022`, and `GRAPHOS-IDENTITY-R023` map to IA requirements in `spec.md`. Source IDs identify intended work, not completion. Cross-repository owners implement their side of the contract stated in this spec; GraphOS tasks cannot close their delivery without their own accepted evidence.

## Strict producer follow-through

- [ ] Qualify EG credential-derived context and authoritative source expiry;
  reject bare lookup and missing generated capability.
- [ ] Inject existing verified broker and secret-backed issuer, preserving the
  strict prepared claims and exact scope ceiling without local grants.
- [ ] Qualify EG session reference/expiry/pending/MFA response and atomic rotation.
- [ ] Implement the existing WebUI evidence port with request-local private
  bindings, after-await rechecks and exact C caller-session-instance pairing.
- [ ] Prove cleanup, cancellation, refresh and concurrent request isolation at
  the mounted E/C integration boundary before claiming browser availability.

## Decomposition children (tracked)

- [x] **GRAPHOS-IDENTITY-R004.1:** Typed loopback bootstrap-principal model with refusal tests
- [x] **GRAPHOS-IDENTITY-R004.2:** Bootstrap resolution path, Host/Origin validation, unsecured-mode indicators
- [x] **GRAPHOS-IDENTITY-R006:** Optional MFA with TOTP, WebAuthn, and recovery codes (rollup)
- [x] **GRAPHOS-IDENTITY-R006.1:** Typed MFA enrollment model with refusal tests
- [x] **GRAPHOS-IDENTITY-R006.2:** TOTP, WebAuthn, recovery-code ceremonies and group enforcement
- [x] **GRAPHOS-IDENTITY-R007:** Admin console tabs with mapping dry-run and mode wizard (rollup)
- [x] **GRAPHOS-IDENTITY-R007.1:** Typed admin-console tab model with refusal tests
- [x] **GRAPHOS-IDENTITY-R007.2:** Mapping dry-run preview and mode-transition wizard
- [ ] **GRAPHOS-IDENTITY-R008.1:** Typed API-key grant model with refusal tests
- [x] **GRAPHOS-IDENTITY-R008.2:** Scope intersection at use time and immediate revocation
- [x] **GRAPHOS-IDENTITY-R009:** Multi-provider OIDC with mapping rules and JIT policy
- [x] **GRAPHOS-IDENTITY-R009.1:** Typed OIDC mapping-rule model with refusal tests
- [ ] **GRAPHOS-IDENTITY-R009.2:** OIDC authentication flow (rollup)
- [ ] **GRAPHOS-IDENTITY-R009.2.1:** Ordered claim-to-rule evaluation
- [ ] **GRAPHOS-IDENTITY-R009.2.2:** ID-token validation inputs and typed refusal
- [ ] **GRAPHOS-IDENTITY-R009.2.3:** Callback handler wiring, link migration, hinted logout
- [ ] **GRAPHOS-IDENTITY-R009.2.4:** Live IdP probe
- [ ] **GRAPHOS-IDENTITY-R010:** LDAPS bind with nested group sync
- [ ] **GRAPHOS-IDENTITY-R010.1:** Typed LDAPS bind-config model with refusal tests
- [ ] **GRAPHOS-IDENTITY-R010.2:** Directory bind, filter escaping, nested-group sync, deprovisioning (rollup)
- [x] **GRAPHOS-IDENTITY-R010.2.1:** Group-DN-to-role mapping evaluation with refusal on no match
- [x] **GRAPHOS-IDENTITY-R010.2.2:** Bind through injected directory port, filter escaping
- [ ] **GRAPHOS-IDENTITY-R010.2.3:** Scheduled nested-group sync and deprovisioning (rollup)
- [x] **GRAPHOS-IDENTITY-R010.2.3.1:** Pure directory sync plan with empty-snapshot refusal
- [ ] **GRAPHOS-IDENTITY-R010.2.3.2:** Schedule the sync and apply the plan through the identity port
- [ ] **GRAPHOS-IDENTITY-R010.2.4:** Live directory probe
- [ ] **GRAPHOS-IDENTITY-R011.1:** Typed SCIM service-credential model with refusal tests
- [ ] **GRAPHOS-IDENTITY-R011.2:** SCIM create/update/patch/deactivate server surface (rollup)
- [x] **GRAPHOS-IDENTITY-R011.2.1:** Typed SCIM User resource model with refusal tests
- [x] **GRAPHOS-IDENTITY-R011.2.2:** SCIM credential check via `ScimServiceCredential.authorizes`
- [ ] **GRAPHOS-IDENTITY-R011.2.3:** SCIM create/patch/deactivate handlers through an injected identity port
- [ ] **GRAPHOS-IDENTITY-R011.2.4:** SCIM route registration
- [ ] **GRAPHOS-IDENTITY-R012:** Native SAML service provider (rollup)
- [ ] **GRAPHOS-IDENTITY-R012.1:** Typed SAML service-provider model with refusal tests
- [ ] **GRAPHOS-IDENTITY-R012.2:** Assertion verification and SAML sign-in (rollup)
- [ ] **GRAPHOS-IDENTITY-R012.2.1:** Typed parsed-assertion model and audience/recipient/time-window checks
- [ ] **GRAPHOS-IDENTITY-R012.2.2:** Signature verification through an injected verifier port
- [ ] **GRAPHOS-IDENTITY-R012.2.3:** Replay check and sign-in handler wiring
- [ ] **GRAPHOS-IDENTITY-R012.2.4:** Live IdP sign-in probe
- [ ] **GRAPHOS-IDENTITY-R016:** Optional SMTP adapter for identity email
- [ ] **GRAPHOS-IDENTITY-R016.1:** Remaining scope of GRAPHOS-IDENTITY-R016 (slice .1): Optional SMTP adapter for identity email
- [ ] **GRAPHOS-IDENTITY-R016.2:** Remaining scope of GRAPHOS-IDENTITY-R016 (slice .2): Optional SMTP adapter for identity email
- [ ] **GRAPHOS-IDENTITY-R017.1:** Remaining scope of GRAPHOS-IDENTITY-R017 (slice .1): Remove the unauthenticated opt-out once clients migrate
- [ ] **GRAPHOS-IDENTITY-R017.2:** Remaining scope of GRAPHOS-IDENTITY-R017 (slice .2): Remove the unauthenticated opt-out once clients migrate
- [ ] **GRAPHOS-IDENTITY-R004.2:** Bootstrap resolution path, Host/Origin validation, unsecured-mode indicators
- [ ] **GRAPHOS-IDENTITY-R006:** Optional MFA with TOTP, WebAuthn, and recovery codes (rollup)
- [x] **GRAPHOS-IDENTITY-R006.1:** Typed MFA enrollment model with refusal tests
- [ ] **GRAPHOS-IDENTITY-R006.2:** TOTP, WebAuthn, recovery-code ceremonies and group enforcement
- [ ] **GRAPHOS-IDENTITY-R007:** Admin console tabs with mapping dry-run and mode wizard (rollup)
- [x] **GRAPHOS-IDENTITY-R007.1:** Typed admin-console tab model with refusal tests
- [ ] **GRAPHOS-IDENTITY-R007.2:** Mapping dry-run preview and mode-transition wizard
