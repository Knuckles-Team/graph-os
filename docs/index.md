# graph-os

graph-os is the **deployable composition** for the agent-utilities agent plane:
the MCP server, REST gateway, control plane, fleet gateway, agent-webui
hosting, and deployment tooling that a running graph-os instance is made of.

This split is defined by **RF-ADR-009**
(`plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md` in the workspace) —
see that document for the full five-layer decision (epistemic-graph,
agent-connector-sdk, agent-utilities, graph-os, the UIs) and its rationale.

This site is generated from the `docs/` sources in this repository and
published via GitHub Pages CI (`.github/workflows/pages.yml`) — per RF-ADR-009
§5, this repository's documentation surface is the **published site**, not a
`/docs` folder read directly on GitHub.

See [Status](status.md) for where this repository stands today.

The deployment lane is the first W5 host-mechanics extraction. Its `setup-config`,
`agent-utilities-doctor`, `agent-utilities-venv`, `graph-os-release-canary`, and
`graph-os-production-ops` scripts are implemented in `graph_os.deployment`;
service/MCP cutover remains a separate lane.
