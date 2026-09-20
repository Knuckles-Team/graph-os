# graph-os

The deployable composition for the agent-utilities agent plane: MCP server, REST
gateway, control plane, fleet gateway, agent-webui hosting, and deployment
tooling. See [`AGENTS.md`](AGENTS.md) for scope, ownership, and status.

Part of the RF-ADR-009 repository split
(`plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md`). This repository is a
in Migration Wave 5. The REST gateway and `graph-os-daemon` host now live in
`graph_os.gateway`; the deployed service continues to use the AU entrypoints
until the coordinated consumer cutover.

*Version: 0.1.0*
