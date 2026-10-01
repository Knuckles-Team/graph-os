# Delivery tasks — GRAPHOS-IDENTITY-ACCESS

State: READY FOR IMPLEMENTATION. Check a task only with an exact commit and test reference in `evidence.md`.

- [ ] **GIA-01 (IA-01/02):** Define engine identity/access DTOs, typed errors, scope classes and test fixture; implement durable mode read/CAS and local issuer key-store adapter. Prove IT-01.
- [ ] **GIA-02 (IA-03/14):** Add identity ports and one serving composition root; route `none` bootstrap requests through the normal signed engine session; enforce bind/Host/Origin policy and warning surfaces. Prove IT-02 and IT-13.
- [ ] **GIA-03 (IA-04):** Implement local account and session ceremonies with hashed server-side IDs, CSRF, rate limits, rotation, revocation and uniform errors. Prove IT-03.
- [ ] **GIA-04 (IA-05):** Add TOTP, WebAuthn, recovery-code and group-MFA policies while preserving action step-up. Prove IT-04.
- [ ] **GIA-05 (IA-06/07):** Build identity self/admin operations and service/API key path; wire REST/MCP/CLI to the same authority. Refuse to advertise unavailable engine-backed methods. Prove IT-05/06.
- [ ] **GIA-06 (IA-12/13):** Build access request, approval, lease, check and explanation service with exact-scope registry and fail-closed fleet/unknown-tool checks. Prove IT-11/12.
- [ ] **GIA-07 (IA-08/09/15):** Add mockable OIDC, LDAP, SCIM, brokered SAML and optional SMTP adapters with strict validation, source-tagged memberships and deprovisioning. Prove IT-07/08/09.
- [ ] **GIA-08 (IA-10):** Add `graph-os identity` commands, configuration defaults, doctor diagnostics and public operator guidance within this repository's GitHub Pages source. Prove CLI portions of IT-09.
- [ ] **GIA-09 (IA-11):** Create and run the three-mode decision table and owned-data transition round trip; use it across the engine and GraphOS contract fixture. Prove IT-10.
- [ ] **GIA-09A (IA-16):** Inventory every GraphOS engine client, carry a verified local-issuer or permitted service token through each one, prove allow/deny and no fallback with IT-14, then coordinate removal of the engine authentication opt-out. Record the exact engine and GraphOS commits separately.
- [ ] **GIA-10:** Run all portable tests on the exact candidate commit; run Ruff, Mypy, hooks, CCCC, KISS, Dupehound, jscpd, frozen lock and wheel checks. Fix findings without weakening thresholds; record each job in `evidence.md`.
- [ ] **GIA-11:** Merge the reviewed implementation to default branch; record exact commit and re-run acceptance evidence against that commit. Update delivery and acceptance states individually.

## Source ID coverage

`GRAPHOS-IDENTITY-R001`, `GRAPHOS-IDENTITY-R002`–`GRAPHOS-IDENTITY-R017`, `GRAPHOS-IDENTITY-R018`, `GRAPHOS-IDENTITY-R019`, `GRAPHOS-IDENTITY-R020`, `GRAPHOS-IDENTITY-R021`, `GRAPHOS-IDENTITY-R022`, and `GRAPHOS-IDENTITY-R023` map to IA requirements in `spec.md`. Source IDs identify intended work, not completion. Cross-repository owners implement their side of the contract stated in this spec; GraphOS tasks cannot close their delivery without their own accepted evidence.
