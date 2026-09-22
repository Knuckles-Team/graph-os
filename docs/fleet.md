# Fleet gateway

`graph_os.fleet` owns the in-process MCP fleet gateway: child lifecycle and
resilience, remote OAuth admission, bounded discovery and health probes,
collision-free tool prefixes, and per-session dynamic loading/unloading.
`MCPMultiplexer` is the native implementation in this repository.

The catalog boundary is `FleetCatalogReader`, which joins EG's live
`RegisterServer` rows with current `AgentComponent` records and verified
content. A serving composition passes that reader to `attach_fleet_loader()`
and awaits `MCPMultiplexer.refresh_engine_catalog()` before starting children.
The reader refuses reads without a verified snapshot and never falls back to a
static config file.

## Served-loop ownership

The FastMCP server owns the only live multiplexer event loop. The colocated
Agent WebUI runs on its own ASGI loop, so its GraphOS-native inventory, call,
and resource helpers submit asynchronous operations through
`graph_os.fleet.shared_multiplexer.run_on_served_multiplexer`. The submitted
coroutine is created as a task on the serving loop with the caller's verified
context; the WebUI awaits the concurrent completion and never blocks a thread
on `Future.result()`. Raw multiplexer access from a foreign loop fails closed.

<ol class="site-flow" aria-label="Fleet serving-loop ownership">
  <li class="site-flow__step"><span class="site-flow__title">Own the loop</span><span class="site-flow__body">FastMCP owns the serving loop and the only live multiplexer.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Submit safely</span><span class="site-flow__body">Agent WebUI submits asynchronous work through the served binding.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Read one snapshot</span><span class="site-flow__body">The multiplexer consumes one verified four-family catalog generation.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Project the fleet</span><span class="site-flow__body">Tools, resources, templates, and prompts remain generation-consistent.</span></li>
</ol>

Catalog reconciliation requires the durable writer to acknowledge the
candidate's exact `catalog_generation` and `snapshot_digest` before atomic
publication. The generic MCP resource/template durability operation is absent
from the current engine contract, so refresh returns the typed
`reingestion-unreconciled` failure. It does not claim convergence or maintain
a second process-local durability store.

The gateway dashboard and Langfuse deployment doctor are consumers of this
same served authority. Widget calls discover the child's admitted schema before
delegation. The doctor proves `langfuse_observability` posture and a bounded
trace read through the child MCP tool. The release canary independently checks
the catalog declaration, launcher, and advertised tool; none of these paths
imports a connector package. Langfuse reads do not trigger a GraphOS-owned
knowledge-graph ingestion side effect: durable source ingestion belongs to
agent-connector-sdk and the epistemic-graph contract.

## Composition invariants

The MCP composition owns `attach_fleet_loader`, installs the gateway
application port, and binds the returned multiplexer through
`graph_os.fleet.shared_multiplexer`. REST and WebUI consumers therefore observe
the same lifecycle, health, OAuth, and discovery state. Co-services use only
the asynchronous submission API.

The EG-backed catalog reader and durable resource/template reconciliation are
explicit capability gates. Without their generated engine contracts, the
affected refresh path returns a typed failure and does not fall back to a
static catalog or process-local durability. See [Capability status](status.md).
