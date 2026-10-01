# GRAPHOS-INGRESS-001 requirements

Every requirement this specification owns, with the proof that closes it. Delivery state and
public evidence for each ID are recorded in [`status.json`](status.json); this file defines what
each ID means. The design is in [`spec.md`](spec.md) and [`plan.md`](plan.md), the test contract
in [`test-spec.md`](test-spec.md), and the work order in [`tasks.md`](tasks.md).

| ID | Requirement | Verification |
|---|---|---|
| `GRAPHOS-INGRESS-R001` | **Public ingress rename with Keycloak and OpenBao cutover.** GraphOS's public ingress is served from its own hostname with a renamed TLS certificate, ingress resource and secret, backed by an OpenBao-stored key delivered through an ExternalSecret, updated Keycloak redirect URIs, and the corresponding host-path and environment configuration; a time-bounded redirect from the prior hostname is available during cutover. | A served probe completes a browser login through Keycloak against the new hostname and confirms the MCP and REST endpoints respond under the renamed ingress. |
