# Identity and authentication

> The identity broker, local users, and mode transitions are available from the identity-broker release. Check the installed release and [capability status](status.md) before following this runbook. Earlier releases use the external OIDC configuration described in [deployment](deployment.md).

Graph OS keeps identity state in the engine and serves browser sign-in from its own identity broker. The durable mode is `none`, `local`, or `external`. `GRAPHOS_AUTH_MODE` seeds only a fresh store; a later environment edit does not change the stored mode. Use `graph-os-identity transition` to change it. The engine still applies the same scopes and RBAC decisions in each mode.

## Before first boot

1. Generate the configuration for the chosen profile and run `setup-config doctor --profile <profile>`. A production profile must not use an automatically generated `GRAPH_SERVICE_AUTH_SECRET` or an exposed `none` mode.
2. Deliver the engine data-key reference, graph-os service credential, issuer signer material, `GRAPHOS_IDENTITY_ISSUER`, `GRAPHOS_IDENTITY_TENANT`, and optional `GRAPHOS_SETUP_CODE` through the deployment's secret backend. Store references in configuration, not secret values. The graph-os service credential must match the engine's copy.
3. Put a TLS endpoint in front of any non-loopback browser listener. Keep the `none` profile loopback-only. Do not use `GRAPHOS_AUTH_NONE_EXPOSE` to turn a demo into a production deployment.
4. Back up the engine store and test restore before changing a live identity mode. Record the running image digest as the rollback anchor.

The first administrator for a fresh `local` or `external` store uses `/auth/setup` and the one-time setup code. A `none` install begins with `usr:bootstrap`; claim it with `graph-os-identity claim --username <name>` before switching to local. The CLI prompts for passwords and second factors. Do not pass them on a command line or write them into a log.

## Harden a demo install

1. Run `setup-config doctor` and confirm the stored mode, bind address, issuer, tenant, secret delivery and policy status.
2. Claim the bootstrap administrator, choose an administrator-only registration policy, and enrol MFA. Administrators and approvers must have MFA. Keep recovery codes in the operator's secret store.
3. If email is unavailable, use administrator-issued one-time reset tokens and recovery codes. A reset token is shown once; a reset also revokes the user's sessions.
4. Rotate any auto-generated graph-os service secret in both graph-os and the engine, then restart the affected units together.
5. In local mode, prove a normal user and an administrator can sign in through the browser. Inspect `/auth/session` after each sign-in; a health request or a service-token call does not prove human login.
6. Configure an external IdP only after its issuer, redirect URI, trust roots and scope mappings are reviewed. Dry-run mapping rules on sample claims, especially rules that could grant `identity:admin` or an approver group.
7. Link the existing administrator to the IdP before transition. Run the link migration dry-run and require **zero ownership changes**. Then transition to `external` with `local_fallback=break-glass` and verify both the external browser path and the MFA-protected local break-glass account.
8. Export and verify the identity audit chain. Recheck sign-in, session revocation and engine authorization after the cutover.

Mode transitions rotate the local issuer signer and revoke sessions. Plan a maintenance window and a fresh administrator sign-in. A binary rollback cannot undo an identity-store transition; use the stored-mode CLI and an operator-tested recovery path. Never create a second identity store or copy a production reset token into a test environment.

## Browser acceptance

A qualifying probe exercises the actual sign-in form or IdP redirect, then requests `/auth/session` with the resulting browser cookie. It asserts the expected principal and server-resolved role. The local path also completes MFA for the test administrator. The external path starts at `/auth/oidc/{idp_id}/login`, follows the IdP authorization-code redirect, and verifies the callback sets `__Host-graphos_session`. A successful discovery document or Keycloak token endpoint is preparatory evidence only.

Use dedicated, non-production test users and keep their credentials in the deployment secret backend. Run the browser probe again against the production URL after rollout; a test namespace pass alone does not qualify production. Keep probe output free of cookies, tokens, credentials, and one-time codes.

For scopes, approver groups, service identities and mode-specific troubleshooting, see the deployment skill's `identity-and-access.md` reference. Browser UI role rendering never grants engine authority; the API and engine check the caller on every operation.
