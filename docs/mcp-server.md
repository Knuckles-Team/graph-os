# MCP server composition

`graph_os.mcp_server` owns the graph-os transport and composition boundary. The
action implementations remain in the agent plane and epistemic-graph; graph-os
registers them once and exposes the same core through MCP and REST.

<ol class="site-flow" aria-label="MCP serving composition">
  <li class="site-flow__step"><span class="site-flow__title">Start</span><span class="site-flow__body">The <code>graph-os</code> command starts the single serving lifecycle.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Compose</span><span class="site-flow__body">The server binds the shared tool and REST application runtime.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Connect authority</span><span class="site-flow__body">Typed agent-utilities services and epistemic-graph clients supply domain behavior.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Open the fleet</span><span class="site-flow__body">Lazy fleet meta-tools expose admitted connector services.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Host co-services</span><span class="site-flow__body">Agent WebUI and messaging use the same composed process authority.</span></li>
</ol>

The serving lifecycle mints process authority, bootstraps the engine, installs
the gateway application adapter, attaches the lazy fleet surface, starts the
native WebUI host when configured, and then serves `stdio` or
`streamable-http`. Optional WebUI imports remain lazy.

The fleet loader uses GraphOS's native multiplexer. Its EG-backed catalog read
is capability-gated on the generated kind-only `AgentComponent.Search` client
contract. When that authority is unavailable, catalog refresh fails closed; it
does not substitute a static reader. See [Capability status](status.md).

## Resident tool surface

The server registers exactly ten resident tools: the six MCP verbs
`find`, `ask`, `why`, `write`, `act`, `manage`, plus the four fleet
meta-tools `find_tools`, `load_tools`, `unload_tools`, `multiplexer_status`.
No other MCP tool is registered; every other capability is reached through
`act`/`manage` or the generated `/api/v1` HTTP surface mounted alongside the
explicit protocol-route table (`graph_os.api.http.protocol_routes`).

The four fleet meta-tools map to internal fleet operations as follows — this
is the published parity inventory for the resident-fleet slice of the
tool-to-operation parity requirement:

| Fleet meta-tool | Destination operation |
|---|---|
| `find_tools` | `fleet.catalog.search` |
| `load_tools` | `fleet.tools.load` |
| `unload_tools` | `fleet.tools.unload` |
| `multiplexer_status` | `fleet.status` |
