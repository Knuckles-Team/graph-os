# Capability status

GraphOS is version `0.1.0` and carries a pre-alpha package classifier. Its main
runtime packages are implemented and tested. It is not yet correct to describe
every end-to-end capability as complete: several paths require public contracts
that are still being completed in adjacent repositories.

GraphOS treats an unavailable authority as a typed, fail-closed result. It does
not substitute a static catalog, process-local store, legacy import, or
fabricated success receipt.

## Implemented surfaces

| Package | Current authority |
|---|---|
| `graph_os.mcp_server` | Native MCP composition, process authority, action routing, and `stdio`/`streamable-http` serving lifecycle |
| `graph_os.fleet` | MCP child lifecycle, health, OAuth admission, collision-safe naming, and per-session discovery/loading |
| `graph_os.gateway` | REST routes, dashboard aggregation, widget projection, and daemon lifecycle |
| `graph_os.control_plane` | Fleet reconciliation and action-policy enforcement |
| `graph_os.webui_host` | Optional Agent WebUI co-service supervision |
| `graph_os.deployment` | Configuration, diagnostics, managed environments, release canary, and production operations |
| `graph_os.a2a` | Authenticated Agent Card plus unary send/get/list/cancel over durable WorkItems |
| `graph_os.browser_control` | Attended catalog, lease, policy, dispatch, cancellation, and provenance orchestration |

MCP and REST handlers meet at the same application boundary. Dashboard
connector widgets delegate through admitted fleet tools rather than importing
connector clients. The WebUI co-service submits MCP inventory and invocation
work to the exact multiplexer instance owned by the serving loop.

## Capability-gated paths

| Capability | Current behavior | Required authority |
|---|---|---|
| EG-backed fleet and browser catalog | Native startup injects the verified process clients into the generated registry/AgentComponent adapter, verifies an exhaustive stable snapshot, composes the public agent/workflow read ports, and refreshes before readiness; any missing authority aborts startup | Released `ListRegisteredServers` and typed AgentComponent-current generated contracts plus the matching AU public catalog port release |
| Authenticated connector runner | GraphOS validates the endpoint, secret reference, request context, and scope before injecting its EG client and dynamic pack-import authority into the SDK; missing authority is refused | Released generated ConnectorPack/SourceIngest contracts, the matching SDK release, and a live catalog/policy resolver |
| Semantic content packs | The serving lifecycle performs read-only status and GraphSchema checks for the independently identified `graph-os` and `agent-utilities` packs; it refuses absent, unprojected, or stale attachments and never imports or attaches during startup | A deployment provisioning pass with policy-issued ConnectorPack mutation contexts and GraphSchema write authority |
| WebUI GraphOS route composition | GraphOS supplies one public application composer; it does not patch WebUI internals or restore WebUI-owned gateway fallbacks | An agent-webui release exposing the matching `application_composer` factory seam |
| Durable four-family catalog reconciliation | Refresh returns `reingestion-unreconciled`; it does not claim publication | Governed generic MCP resource/template durability in epistemic-graph |
| A2A budget-selected tool subset | A request with a context budget or non-empty selected-tool set is refused before admission | Live `AgentAssemble` support and a signed agent envelope that binds the allowed tool subset |

Ordinary authenticated A2A routing to an existing authorized agent remains
available. Streaming, push notifications, and task transition history are not
advertised by the Agent Card.

## Release posture

The repository builds a Python wheel and publishes only from a version tag
after the release workflow succeeds. A green `main` build is not itself a PyPI
release. The status badges and package links populate after the first tagged
publication.

Before deploying a candidate, validate its configuration, run the deployment
doctor, and execute the release canary described in the
[deployment guide](deployment.md).
