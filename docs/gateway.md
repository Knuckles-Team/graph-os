# REST gateway

`graph_os.gateway` owns HTTP routing, dashboard aggregation, the widget
registry, and the `graph-os-daemon` host lifecycle. Agent, graph, ontology,
catalog, and policy behavior remains owned by agent-utilities or
epistemic-graph.

The application boundary is `GatewayApplicationPort`. The composition root
installs one implementation with `configure_gateway_application()` before it
calls `register_graph_routes()`.

<ol class="site-flow" aria-label="REST gateway composition">
  <li class="site-flow__step"><span class="site-flow__title">Receive HTTP</span><span class="site-flow__body">The GraphOS gateway receives an authenticated application request.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Cross one port</span><span class="site-flow__body">GatewayApplicationPort connects routes to the shared application runtime.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Use one composition</span><span class="site-flow__body">MCP and fleet services share the same GraphOS process state.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Reach the owner</span><span class="site-flow__body">Typed agent-utilities and epistemic-graph contracts perform domain work.</span></li>
</ol>

There is no fallback import of AU's `kg_server` or multiplexer. An unconfigured
process fails closed before routes are served, so the active authority remains
observable rather than silently selecting another implementation.

## Connector widgets

Dashboard widgets retain only presentation and response-projection logic in
`graph_os.gateway.widgets`. They do not import connector packages or
construct vendor API clients. Each connector operation is resolved from the
served multiplexer tool catalog and invoked with
`MCPMultiplexer.delegate_server_tool()` through
`run_on_served_multiplexer()`. Credentials, TLS, child lifecycle, and tool
admission therefore remain at the connector/fleet boundary.

<ol class="site-flow" aria-label="Connector widget request flow">
  <li class="site-flow__step"><span class="site-flow__title">Schedule</span><span class="site-flow__body">The dashboard aggregator assigns work to a bounded widget worker.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Project</span><span class="site-flow__body">GraphOS retains only presentation and response projection.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Resolve</span><span class="site-flow__body">The served multiplexer resolves the operation from the verified catalog.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Delegate</span><span class="site-flow__body">The admitted connector child performs the source-specific operation.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Return</span><span class="site-flow__body">The response returns through the same multiplexer and projection.</span></li>
</ol>

A connector absent from the EG-backed catalog, an ambiguous operation, or a
child failure produces the widget's existing correlation-safe error shape.
GraphOS never falls back to package importability or direct vendor credentials.
