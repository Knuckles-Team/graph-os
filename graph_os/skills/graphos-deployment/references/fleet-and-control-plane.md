# Fleet multiplexer and the MCP/API control plane

## Surfaces

graph-os exposes one backend through three projections that must agree:

- **MCP** (`streamable-http` on the API port, or `stdio` for a local client):
  today the native `graph_*` verbs plus the four resident fleet multiplexer
  meta-tools;
- **REST/A2A control plane** on the same port;
- **web UI** on its own port, served in-process (`ENABLE_WEB_UI=true`,
  `GRAPH_OS_WEBUI_PORT`). Never deploy the UI as a second workload: it would
  need its own engine identity.

Today the MCP surface is the native `graph_*` verbs and the REST routes the
release documents. A registry-backed MCP/API intent projection exists in code
(typed HTTP routes under `/api/v1/ops/{op}` with OpenAPI, and MCP intent verbs
over one registry so the surfaces cannot drift) but is not yet mounted into
the served app — see **Not available yet** below.

## Fleet multiplexer

graph-os connects to the fleet of MCP connectors (from its MCP configuration,
`MCP_CONFIG`) and reaches it through the intent tools: `find(action="tools"|
"catalog"|"status")` discovers, `act(action="fleet.call")` calls and
`manage(action="fleet.load"|"fleet.unload")` mounts or releases. No fleet tool
is added to the served tool list. Discovery spans fleet tools, agent skills,
prompts, resources and connector-SDK items. Mounted servers expire with the
session (`MCP_SESSION_IDLE_TTL_SECONDS`, default 3600).

Fleet access needs two **exact** scopes on the caller: `mcp:discover` (find,
list) and `mcp:delegate` (load, call). Administrative scopes such as `kg:admin`
do not imply them: grant them deliberately to the people and clients that
should reach the fleet, and to the local process session of a tiny/stdio
install. Request-time Eunomia filtering of find/list/load/call by policy, on
top of these scopes, is not available yet (see below).

Wiring rules:

- register every connector with its transport URL and required scopes; a
  connector whose server URL is on a private network must be allowed by the
  fleet's private-host allowlist;
- connectors that read/write the graph dial the engine's TLS listener (or go
  through graph-os), never a second engine;
- a connector that looks `up` in `find(action="status")` but fails every call
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

## Not available yet

- a registry-backed MCP/API intent projection mounted into the served app
  (every capability — identity and user management, capacity, leases and
  elevation, federation, ingest, Decide, analytics — as one registry operation
  projected to MCP intent verbs, typed HTTP routes and A2A through one
  authority chokepoint); administrative and approval actions started over MCP
  or A2A returning `STEP_UP_REQUIRED` with a console link;
- request-time Eunomia filtering of fleet find/list/load/call by policy;
- graph-os's service identity holding the four capacity scopes
  (`capacity:throttle|admin|lease|read`, exact, no wildcard);
- a principal-scoped lease-kind allowlist
  (`EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON`) restricting which lease
  kinds graph-os's own identity may write; today graph-os's lease writes are
  not kind-restricted.

Resource leases (model reservations, training capacity) use the engine's
capacity cells and fenced, idempotent leases; the engine enforces reserved
floors independently of the graph-os-side scopes above.
