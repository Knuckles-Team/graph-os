"""GraphOS: the deployable composition for the Knuckles agent platform.

RF-ADR-009 (`plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md`) §2 assigns
this repository phase 5: **the deployable composition** — the MCP server, REST
gateway, control plane, fleet gateway, agent-webui hosting, and deployment
tooling. It must never own business logic that belongs to
epistemic-graph, agent-connector-sdk, or the agent-utilities agent plane.

The live package layout is:

- `graph_os.mcp_server` — native MCP/REST composition and serving lifecycle.
- `graph_os.fleet` — in-process MCP fleet gateway and child lifecycle.
- `graph_os.gateway` — REST gateway and dashboard projections.
- `graph_os.control_plane` — reconciliation and action policy.
- `graph_os.webui_host` — optional Agent WebUI co-service hosting.
- `graph_os.deployment` — configuration, diagnostics, canaries, and operations.

The ``graph-os`` console command serves
``graph_os.mcp_server.server:mcp_server`` over stdio or authenticated
streamable HTTP. Capability limits that depend on unfinished upstream
contracts are documented in ``docs/status.md`` and fail closed; no legacy
fallback is implied by this package description.
"""

from graph_os._version import __version__

from . import epistemic

__all__ = ["__version__", "epistemic"]
