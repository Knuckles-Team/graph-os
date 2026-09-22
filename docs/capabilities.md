# Capabilities

Graph OS exposes one governed runtime across the platform's supported
transports and hosted interfaces. The [status page](status.md) identifies the
exact surface shipped by the current release.

| Capability | What Graph OS provides | Detailed guide |
|---|---|---|
| MCP | Local `stdio` and authenticated streamable HTTP transports | [MCP server](mcp-server.md) |
| REST | HTTP projection of the shared application services | [REST gateway](gateway.md) |
| A2A | Unary task submission through the governed runtime | [A2A](a2a.md) |
| Fleet | Discovery, admission, supervision, and routing for connector servers | [Fleet gateway](fleet.md) |
| Identity and policy | Tenant context, action policy, idempotency, and provenance | [Architecture](architecture.md) |
| Hosted interfaces | Agent Web UI plus terminal, desktop, messaging, and attended browser entrypoints | [Interfaces](interfaces.md) |

Use the [deployment guide](deployment.md) to configure identity, TLS, runtime
authorities, and the desired interface bundle.
