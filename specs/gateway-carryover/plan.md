# GRAPHOS-GWC-001 — Design and implementation plan

Status: PROPOSED. Governing spec: [spec.md](spec.md).

## Functional description

Deleted dashboard router (prefix `/api/dashboard`, read capability on every route except `/health`; write capability on mutations; capabilities gateway:read, gateway:write, gateway:admin, admin; API-key callers unrestricted):
- GET `/layout` and PUT `/layout`: read and persist the dashboard layout (shared YAML, reads always go back to disk).
- GET `/data`, GET `/data/{service_id}`: widget data for all or one service; the id must match a safe pattern, unknown service returns 404.
- GET `/full`: layout and data in one call. GET `/data-subset?widget_id=...`: only the named widgets, fetched concurrently, never computing unsubscribed ones.
- GET `/widgets`: widget types available. GET `/discover`: layout auto-discovered from the MCP config.
- GET `/health`: always 200 with the shared truthful health report, `Cache-Control: no-store`.
- GET `/daemon/status`, GET `/daemon/shards`, POST `/daemon/start`: consolidated background daemon state, engine shard topology and reachability, ensure daemon running.
- POST `/hydrate/{source}`, POST `/hydrate`, GET `/hydration-status`: trigger hydration for one or all sources, and read source status; 422 on a malformed source, 500 when no active engine, sanitized error payloads.

Deleted Live Artifacts router (no router-level auth in the old module):
- POST `/api/artifacts`: create from name, template, data, source query, source node ids and model; provenance records model, query and evidence ids; bounded-JSON violation returns 400; response is artifact id and rendered output.
- GET `/api/artifacts/{artifact_id}`: full artifact, 404 when absent.
- POST `/api/artifacts/{artifact_id}/refresh`: with inline `data` the body becomes the new derivation; otherwise a registered source resolver re-derives from the graph (the default resolver preserved prior data). Returns ok, reason and rendered output; 404 when absent; a failed refresh preserves the prior render.
- Stores: the shared Live Artifact store and refresh service, a module singleton. Caller: `server/app.py` mount only; the KG source installer was deleted with the router.

Deleted Genius Agent widget: dashboard tile (type genius_agent, env prefix GENIUS_AGENT, observability category) with fields agents, skills, mcp_tools and status. Its fetch returned hardcoded numbers and never contacted anything.

## Design in this repository

- Dashboard: owned by the existing dashboard router module and its aggregator, config manager and registry under the gateway package. No new module; add census and capability tests only.
- Live Artifacts: new router module `graph_os/gateway/artifacts_api.py` (to be created) with typed request model, a store port and a refresh port declared in the gateway ports module, and a register function that mounts the three routes. The store and refresh service come from a public `agent_utilities.api` export (prerequisite row); graph-os never imports the agent-utilities knowledge-graph package directly.
- The source resolver is a port argument, not a module global: the host passes a graph-backed resolver, default behaviour is preserve-prior-data.
- Genius Agent widget: nothing to build.

## Wiring

- Dashboard: the register function in the dashboard router module mounts routes and the websocket; already called by the host.
- Artifacts: a new register function is called from the same host composition point that mounts the graph and usage routes. Scopes: reuse the dashboard read and write capability sets already in the engine contract (gateway:read for GET, gateway:write for POST); the old router had none, so this is an intentional tightening, called out in the parity table.
- Config: the artifact store location follows the agent-utilities store default; no new env key.

## Parity table

| Old entry point | New entry point | Status | Reason |
|---|---|---|---|
| GET /api/dashboard/layout, PUT /layout | dashboard router same paths | exists | verify with a test |
| GET /data, /data/{service_id}, /full, /data-subset | dashboard router same paths | exists | verify with a test |
| GET /widgets, /discover, /health | dashboard router same paths | exists | verify with a test |
| GET /daemon/status, /daemon/shards, POST /daemon/start | dashboard router same paths | exists | verify with a test |
| POST /hydrate/{source}, POST /hydrate, GET /hydration-status | dashboard router same paths | exists | verify with a test |
| websocket /ws/dashboard (consumer of the subset fetch) | dashboard websocket | exists | verify with a test |
| fetch_dashboard_subset (Python helper) | aggregator fetch via /data-subset | exists | helper removed upstream with its test |
| POST /api/artifacts | artifacts router | to build | no equivalent found in graph-os |
| GET /api/artifacts/{id} | artifacts router | to build | no equivalent found |
| POST /api/artifacts/{id}/refresh | artifacts router | to build | no equivalent found |
| register_artifact_source | resolver port argument | to build | replaces a module global |
| genius_agent widget | none | intentionally dropped | constant fabricated data; fleet and catalog surfaces report real counts |

## Out of scope

Moving the Live Artifact store or refresh service out of agent-utilities, new dashboard widgets, any change to the layout file format, and the MCP tool surface for artifacts (the research-artifact tool is unrelated).
