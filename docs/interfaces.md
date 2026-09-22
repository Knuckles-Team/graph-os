# Interfaces

Every Graph OS interface reaches the same governed application services. Choose
the transport that matches the caller; identity, tenant policy, and provenance
remain part of the request lifecycle.

| Caller | Interface | Start here |
|---|---|---|
| Local MCP clients | `stdio` | [MCP server](mcp-server.md) |
| Remote MCP clients | Authenticated streamable HTTP | [MCP server](mcp-server.md) |
| HTTP applications and hosted clients | REST | [REST gateway](gateway.md) |
| Agent peers | Unary A2A | [A2A](a2a.md) |
| Browser users | Hosted Agent Web UI and attended browser control | [Browser control](browser-control-service.md) |
| Operators | Health, readiness, and fleet surfaces | [Capability status](status.md) |

Agent Web UI, Agent Terminal UI, Geniusbot, and messaging channels are client
experiences at this boundary. Graph OS owns their governed gateway; each client
owns its presentation and local interaction state.
