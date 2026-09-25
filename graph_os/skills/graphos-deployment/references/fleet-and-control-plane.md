# Fleet multiplexer and the MCP/API control plane

## Surfaces

graph-os exposes one backend through three projections that must agree:

- **MCP** (`streamable-http` on the API port, or `stdio` for a local client):
  the six intent verbs plus four resident fleet multiplexer meta-tools
  after the operation-API cutover;
- **REST/A2A control plane** on the same port;
- **web UI** on its own port, served in-process (`ENABLE_WEB_UI=true`,
  `GRAPH_OS_WEBUI_PORT`). Never deploy the UI as a second workload: it would
  need its own engine identity.

From **Wave C (MCP intent surface)**, every capability — including identity and
user management, capacity, leases and elevation, federation, ingest, Decide,
analytics — is one registry operation projected to MCP intent verbs, typed
HTTP routes (`/api/v1/ops/{op}`, OpenAPI) and A2A (`graphos.op/invoke`)
through one authority chokepoint, so the surfaces cannot drift.
Administrative and approval actions started over MCP or A2A return
`STEP_UP_REQUIRED` with a console link; the operator confirms in the browser.
Before Wave C, the MCP surface is the native `graph_*` verbs and the REST
routes the release documents.

## Fleet multiplexer

graph-os connects to the fleet of MCP connectors (from its MCP configuration,
`MCP_CONFIG`) and exposes resident meta-tools — `find_tools`, `load_tools`,
`unload_tools`, `multiplexer_status` — plus `fleet.call`. Discovery spans fleet
tools, agent skills, prompts, resources and connector-SDK items. Loading mounts
the chosen tools as real MCP tools for that session (`tools/list_changed`),
capped per session (`MCP_SESSION_LOADED_ITEMS_MAX`, default 64) and expiring
with the session (`MCP_SESSION_IDLE_TTL_SECONDS`, default 3600). With Eunomia
on, find/list/load visibility and every call are filtered per caller (Wave C).

Fleet access needs two **exact** scopes on the caller: `mcp:discover` (find,
list) and `mcp:delegate` (load, call). Administrative scopes such as `kg:admin`
do not imply them (**from Wave B**): grant them deliberately to the people and
clients that should reach the fleet, and to the local process session of a
tiny/stdio install.

Wiring rules:

- register every connector with its transport URL and required scopes; a
  connector whose server URL is on a private network must be allowed by the
  fleet's private-host allowlist;
- connectors that read/write the graph dial the engine's TLS listener (or go
  through graph-os), never a second engine;
- a connector that looks `up` in `multiplexer_status` but fails every call
  usually cannot reach the engine or its IdP — read the **child's** log;
- throttling starts **observe-only**: each child gets a default error budget in
  observe mode (the controller logs the ceiling it would set);
  enforcement is chosen per child after about a week of data. The controller
  reads measurements from `SCALING_PROMETHEUS_URL`.

## Gateway authentication (inbound and outbound)

Inbound: validate issuer, audience, signature and JWKS rotation, time bounds,
tenant, subject, scopes and transport TLS. Outbound: prefer workload identity
or client credentials scoped to the destination; a failed token mint fails
closed and is attributed in the trace — never call a protected child without
authorization. Bind every tool call to the caller and tenant, the selected
server/tool and its registration, the allow-list and consent decision,
validated arguments with an idempotency key, the downstream identity, and a
redacted, correlated trace. Tool annotations received from an MCP server never
grant permission.

Test valid, expired, wrong-audience, wrong-tenant, revoked, missing and
insufficient-scope identities; caches must key on tenant and policy version.

## Capacity and leases

- graph-os's service identity holds the four capacity scopes
  (`capacity:throttle|admin|lease|read`, exact, no wildcard) — **available from
  Wave B**.
- Resource leases (model reservations, training capacity) use the engine's
  capacity cells and fenced, idempotent leases; the engine enforces reserved
  floors.
- graph-os writes leases under its own identity only for the kinds on its
  principal-scoped lease-kind allowlist
  (`EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON`, **available from train 6**).
  Verify an allowed kind succeeds and any other kind is refused.
