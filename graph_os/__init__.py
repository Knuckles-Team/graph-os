"""graph-os: the deployable composition for the agent-utilities agent plane.

RF-ADR-009 (`plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md`) §2 assigns
this repository phase 5: **the deployable composition** — the MCP server, REST
gateway, control plane, fleet gateway, agent-webui hosting, and deployment
tooling that today run out of `agent-utilities` and are deployed by
`services/graph-os`. It must never own business logic that belongs to
epistemic-graph, agent-connector-sdk, or the agent-utilities agent plane.

Package layout mirrors that ownership (see `AGENTS.md` "Module mapping" for the
source each package will receive in Migration Wave 5):

- `graph_os.mcp_server` — the graph-os MCP server
  (today `agent_utilities.mcp.kg_server`).
- `graph_os.fleet` — the in-process fleet gateway / multiplexer
  (today `agent_utilities.mcp.multiplexer`), catalog sourced from the EG
  server registry.
- `graph_os.gateway` — the REST gateway (today `agent_utilities.gateway`).
- `graph_os.control_plane` — the control plane
  (today `agent_utilities.control_plane`).
- `graph_os.webui_host` — agent-webui hosting
  (today `agent_utilities.server.webui_co_service`).
- `graph_os.deployment` — deployment tooling: doctor, release canary,
  production ops (today `agent_utilities.deployment`).

**Status: pre-extraction scaffold (RF-ADR-009 W0/W5-prep).** None of the packages
above carry runtime logic yet — see AGENTS.md "Status" before assuming any
described behavior is live. The one real, tested surface today is the
`graph-os` console script (`graph_os.cli`), which reports this package's
version and the target composition it will host.
"""

from graph_os._version import __version__

__all__ = ["__version__"]
