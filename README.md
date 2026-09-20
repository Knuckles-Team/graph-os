# graph-os

The deployable composition for the agent-utilities agent plane: MCP server, REST
gateway, control plane, fleet gateway, agent-webui hosting, and deployment
tooling. See [`AGENTS.md`](AGENTS.md) for scope, ownership, and status.

Part of the RF-ADR-009 repository split
(`plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md`). This repository is a
Migration Wave 5 extraction. The REST gateway, control plane, WebUI host, and
host-deployment mechanics now live here. The deployed service continues to use
the AU MCP/composition entrypoints until the coordinated consumer cutover.

*Version: 0.1.0*
