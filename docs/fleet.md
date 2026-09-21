# Fleet gateway

`graph_os.fleet` owns the in-process MCP fleet gateway: child lifecycle and
resilience, remote OAuth admission, bounded discovery and health probes,
collision-free tool prefixes, and per-session dynamic loading/unloading.
`MCPMultiplexer` is a native implementation in this repository; it is not an
import or compatibility alias for AU's former multiplexer.

The catalog boundary is `FleetCatalogReader`, which joins EG's live
`RegisterServer` rows with current `AgentComponent` records and verified
content. A serving composition passes that reader to `attach_fleet_loader()`
and awaits `MCPMultiplexer.refresh_engine_catalog()` before starting children.
The reader path fails closed until a verified snapshot exists and never falls
back to a static config file.

## Served-loop ownership

The FastMCP server owns the only live multiplexer event loop. The colocated
Agent WebUI runs on its own ASGI loop, so its GraphOS-native inventory, call,
and resource helpers submit asynchronous operations through
`graph_os.fleet.shared_multiplexer.run_on_served_multiplexer`. The submitted
coroutine is created as a task on the serving loop with the caller's verified
context; the WebUI awaits the concurrent completion and never blocks a thread
on `Future.result()`. Raw multiplexer access from a foreign loop fails closed.

```mermaid
flowchart LR
    MCP[FastMCP lifespan and requests] --> Owner[FastMCP owner loop]
    UI[Agent WebUI ASGI loop] -->|async submit| Binding[served multiplexer binding]
    Binding --> Owner
    Owner --> Mux[one MCPMultiplexer]
    Mux --> Snapshot[one four-family immutable snapshot]
    Snapshot --> Tools[tools]
    Snapshot --> Resources[resources]
    Snapshot --> Templates[resource templates]
    Snapshot --> Prompts[prompts]
```

Catalog reconciliation still requires the durable writer to acknowledge the
candidate's exact `catalog_generation` and `snapshot_digest` before atomic
publication. GraphOS no longer imports AU's source-sync writer. Until
epistemic-graph exposes the governed generic MCP resource/template durability
operation, the writer capability is absent and refresh returns the typed
`reingestion-unreconciled` failure. It does not claim convergence or maintain
a second process-local durability store.

The gateway dashboard and Langfuse deployment doctor are consumers of this
same served authority. Widget calls discover the child's admitted schema before
delegation. The doctor proves `langfuse_observability` posture and a bounded
trace read through the child MCP tool. The release canary independently checks
the catalog declaration, launcher, and advertised tool; none of these paths
imports a connector package. Langfuse reads do not trigger a GraphOS-owned
knowledge-graph ingestion side effect: durable source ingestion belongs to the
connector SDK and epistemic-graph contract.

## Composition invariants

The MCP composition owns `attach_fleet_loader`, installs the gateway
application port, and binds the returned multiplexer through
`graph_os.fleet.shared_multiplexer`. REST and WebUI consumers therefore observe
the same lifecycle, health, OAuth, and discovery state. Co-services use only
the asynchronous submission API.

The EG-backed catalog reader and durable resource/template reconciliation are
explicit capability gates. Until their generated engine contracts are live,
the affected refresh path returns a typed failure and does not fall back to a
static catalog or process-local durability. See [Capability status](status.md).
