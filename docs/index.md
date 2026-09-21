# GraphOS

GraphOS is the deployable composition layer for the Knuckles agent platform. It
hosts the MCP and REST boundaries, supervises the MCP fleet, applies
control-plane policy, optionally co-hosts Agent WebUI, and provides deployment
and health tooling around the agent and graph services.

GraphOS deliberately keeps domain authority elsewhere:

- **epistemic-graph** owns durable graph state, RDF/OWL/SHACL semantics,
  queries, proofs, and provenance;
- **agent-utilities** owns agent decisions and workflows;
- **agent-connector-sdk** and connector services own source-specific transport
  and external effects;
- **agent-webui** owns browser presentation and local interaction state.

GraphOS authenticates, composes, routes, supervises, and projects those
capabilities through one governed runtime.

## Start here

- [Capability status](status.md) — implemented surfaces and explicit upstream
  capability gates.
- [Deployment and configuration](deployment.md) — install, configure, run, and
  validate a GraphOS instance.
- [MCP server](mcp-server.md) — process composition and serving lifecycle.
- [REST gateway](gateway.md) — shared application-service routing.
- [Fleet gateway](fleet.md) — dynamic MCP discovery and lifecycle.
- [Unary A2A](a2a.md) — authenticated agent-to-agent projection.
- [Governed browser control](browser-control-service.md) — attended browser
  capability admission and dispatch.

The documentation is built from this repository with `mkdocs build --strict`
and published by the Pages workflow on changes to `main`.
