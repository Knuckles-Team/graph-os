# GRAPHOS-INGRESS — Portable public ingress and identity cutover

**Owner:** graph-os. **Requirement ID:** GRAPHOS-INGRESS-R001 (GraphOS deployment-consumer partition). **Delivery:** SPECIFIED. **Acceptance:** NOT_AUDITED.

## Purpose and user stories

A contributor can expose a disposable GraphOS instance at a domain they control, connect an OIDC provider they control, and verify the browser and MCP entrypoints without any organization-specific domain, realm, secret store, or cluster. An operator can change a served hostname without changing principal identity or weakening Host, origin, TLS, and engine authorization checks.

P0: A deployment operator supplies a public origin, TLS material by reference, an IdP issuer and client registration, and a GraphOS profile. A reviewer can see the rendered ingress, callback, secret and verification contract before apply. P0: A user completes the login redirect and reaches an authorized GraphOS API; a wrong host, origin, token, tenant, or callback fails closed. P1: A cutover can briefly redirect an old public origin to a new one after both origins and IdP callbacks are explicitly authorized; the redirect expires and does not broaden the API Host allowlist.

## Functional requirements and acceptance

| ID | Requirement | Observable success |
|---|---|---|
| IN-01 | A deployment profile declares one canonical HTTPS origin, exact allowed host, TLS certificate reference, service target, identity provider issuer, client ID, callback URI and client-secret reference. No value is inferred from a local machine or organization convention. | A dry run renders these fields and refuses missing or malformed inputs with section/key diagnostics. |
| IN-02 | The GraphOS serving process and ingress agree on forwarded scheme/host, trusted proxy boundary, Host allowlist, secure cookie origin, callback URL, and WebUI base URL. Proxy headers from an untrusted peer are ignored. | A valid HTTPS request reaches the same GraphOS gateway identity middleware as direct requests; spoofed headers do not change authority. |
| IN-03 | A non-loopback listener requires TLS at the public boundary, validated JWT/JWKS identity, exact Host admission, same-origin cookie mutation, and tenant-scoped authorization. No anonymous fallback or unsigned engine call is introduced. | Authenticated tenant request succeeds; missing/invalid token, wildcard Host, wrong Origin, and cross-tenant request fail before effects. |
| IN-04 | A host cutover requires explicit old and new origins, time-bounded redirect policy, both IdP callback registrations, certificate coverage, and a rollback plan. Redirect only safe browser navigation; never redirect credentials or API mutation requests. | Browser navigation reaches the new origin; old API POST and out-of-window redirect fail without leaking credentials. |
| IN-05 | An independent contributor can use a local OIDC fixture and loopback TLS/ingress fixture for required PR checks. A real provider and DNS certificate are optional release acceptance evidence. | CI runs deterministic positive and negative contract tests without a private cluster, domain or secret store. |
| IN-06 | The deployment doctor and post-apply probe report a redacted profile digest, ingress reachability, TLS hostname match, IdP discovery/callback agreement, authenticated MCP `tools/list`, and a denied request. | One exact-revision receipt distinguishes source checks, rendered checks and served checks; secrets and raw tokens are absent. |

## Architecture, interface, and boundaries

GraphOS owns the deployment profile validation, rendered application settings, gateway identity admission, and health/functional observations. The infrastructure operator supplies a standards-compliant TLS ingress and DNS; the IdP supplies OIDC discovery, signing keys and callback registration. [Access control](../identity-access/spec.md) owns login/session/issuer policy. [Portable deployment](../portable-deployment/spec.md) owns the common profile and installer. The browser owns only rendering and calls the GraphOS public API. The graph engine remains the durable authorization authority.

Profile inputs are generic: `public_origin=https://graph.example.test`, `ingress_host=graph.example.test`, `tls_certificate_ref`, `idp_issuer`, `oidc_client_id`, `oidc_client_secret_ref`, `callback_uri=https://graph.example.test/auth/callback`, `trusted_proxy_cidrs` or a named proxy identity, and `legacy_redirect` with explicit expiry if used. These names define required semantics; `plan.md` determines how they map onto the existing closed profile schema. Secret values never enter a profile, resource annotation, log, or receipt.

Out of scope: provisioning a specific DNS provider, certificate authority, OIDC tenant, or secret backend; building another identity issuer; universal access to private services. The implementation must reuse `graph_os.deployment.genesis_environments`, `config_generator`, `doctor`, and `graph_os.gateway.graph_api` rather than introducing a parallel admission service.

## Success criteria and traceability

| Requirement | Requirement ID | Design | Tests | Acceptance evidence |
|---|---|---|---|---|
| IN-01 | GRAPHOS-INGRESS-R001 | [Profile contract](plan.md#profile-and-interface-contract) | T-IN-01, T-IN-02 | exact commit, rendered fixture |
| IN-02 | GRAPHOS-INGRESS-R001 | [Request path](plan.md#request-and-deployment-path) | T-IN-03, T-IN-04 | integration trace |
| IN-03 | GRAPHOS-INGRESS-R001 | [Security](plan.md#security-and-failure-handling) | T-IN-05, T-IN-06 | denied-request receipts |
| IN-04 | GRAPHOS-INGRESS-R001 | [Cutover](plan.md#cutover-and-rollback) | T-IN-07, T-IN-08 | time-boxed served probe |
| IN-05 | GRAPHOS-INGRESS-R001 | [Portable proof](plan.md#quality-and-release-gates) | T-IN-09 | public CI link |
| IN-06 | GRAPHOS-INGRESS-R001 | [Observability](plan.md#security-and-failure-handling) | T-IN-10 | redacted served receipt |

This spec represents only the GraphOS consumer contract of GRAPHOS-INGRESS-R001. Infrastructure manifests and provider registrations have their own owners and must satisfy this contract before a hosted cutover can be accepted.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
