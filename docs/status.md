# Capability status

This page describes the surface of GraphOS `0.1.0` as it is shipped. GraphOS
reports missing authority as a typed unavailable result. It does not substitute
a static catalog, process-local durable store, or fabricated success receipt.

## Runtime surfaces

| Surface | Availability | Current authority |
|---|---|---|
| MCP composition | Available | One FastMCP lifecycle with `stdio` and authenticated `streamable-http` transports |
| REST gateway | Available | The same application services used by MCP, projected through GraphOS routes |
| Fleet gateway | Available with a verified catalog | Child lifecycle, health, OAuth admission, collision-safe naming, and per-session discovery |
| Agent WebUI host | Available through the `webui` extra | Co-service supervision with an injected GraphOS application composer |
| Unary A2A | Available | Authenticated Agent Card plus send, get, list, and cancel over durable WorkItems |
| Browser control | Available when attended identity is configured | Catalog, lease, policy, dispatch, cancellation, and provenance orchestration |
| Deployment operations | Available | Configuration, diagnostics, release canary, managed environments, backup, and restore validation |

MCP and REST handlers meet at the same application boundary. Connector widgets
delegate through admitted fleet tools. The WebUI co-service submits work to the
same multiplexer owned by the serving loop.

## Composition requirements

| Capability | Required runtime state | Behavior when absent |
|---|---|---|
| EG-backed fleet catalog | Generated server-registration and AgentComponent clients, plus the matching agent-utilities read ports | Startup refuses readiness; no static catalog is selected |
| Connector runner | Verified OIDC actor, tenant-bound EG client, HTTPS GraphOS catalog authority, SDK runner, and admitted pack policy | Startup or import is refused when any authority is absent |
| Semantic content packs | Provisioned `graph-os` and `agent-utilities` packs attached through GraphSchema | Content checks report the missing or stale attachment |
| WebUI application routes | Agent WebUI installed with its application-composer seam | GraphOS runs without the optional WebUI co-service |
| Attended browser control | WebUI identity validation, backchannel revalidation, and engine mutation authorities | Browser control remains unavailable |

## Explicitly unavailable surfaces

| Surface | Current result |
|---|---|
| Durable four-family MCP resource/template reconciliation | `reingestion-unreconciled`; GraphOS does not claim publication |
| Live ConnectorPack catalog import | Source composition exists; deployment remains unavailable until EG request-scoped catalog reads, policy receipts, fleet TLS/CA/DNS, and matching service grants are verified together |
| GraphOS `/api/sparql` compatibility route | Returns `503 sparql_unavailable` after `kg:read` admission; a verified, request-scoped binding to EG's native SPARQL client is not yet composed |
| Context-budget-selected A2A tool subsets | Refused before admission because the signed request does not bind an enforceable subset |
| A2A streaming, push notifications, and transition history | Not advertised by the Agent Card |

Ordinary authenticated unary A2A routing to an authorized agent remains
available.

The mounted MCP process can reconcile one stable child snapshot through EG and
expose `connector_pack_authority` to a separately authenticated runner. Its
request actor authorizes the exact connector and reads the persisted EG binding
and AgentLibrary owner; the GraphOS process attester only reconciles the child
it actually serves. `graph-os-connector-sync` verifies its OIDC token before
opening a tenant-bound EG client, refreshes its client credentials for MCP, and
reconnects before the EG token expires. It sends a bearer to a fleet child only
at the exact `https://<connector>.arpa/mcp` origin. These source paths fail closed
when the live catalog row, grant, TLS route, policy receipt, or matching identity
is unavailable. They have not been accepted against a running fleet.

## Release evidence

The repository builds a Python wheel and publishes from a version tag after the
release workflow succeeds. A green `main` build updates source and documentation
without publishing a package.

Before changing live traffic, validate the resolved configuration, run the
deployment doctor, and execute the release canary described in the
[deployment guide](deployment.md).
