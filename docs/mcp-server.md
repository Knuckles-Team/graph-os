# MCP server composition

`graph_os.mcp_server` owns the graph-os transport and composition boundary. The
action implementations remain in the agent plane and epistemic-graph; graph-os
registers them once and exposes the same core through MCP and REST.

<ol class="site-flow" aria-label="MCP serving composition">
  <li class="site-flow__step"><span class="site-flow__title">Launch</span><span class="site-flow__body">The <code>graph-os</code> command starts the single serving lifecycle.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Compose</span><span class="site-flow__body">The server binds the shared tool and REST application runtime.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Connect authority</span><span class="site-flow__body">Typed agent-utilities services and epistemic-graph clients supply domain behavior.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Open the fleet</span><span class="site-flow__body">Lazy fleet meta-tools expose admitted connector services.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Host co-services</span><span class="site-flow__body">Agent WebUI and messaging use the same composed process authority.</span></li>
</ol>

The serving lifecycle mints process authority, bootstraps the engine, installs
the gateway application adapter, attaches the lazy fleet surface, starts the
native WebUI host when configured, and then serves `stdio` or
`streamable-http`. Optional WebUI imports remain lazy. On shutdown it drains
and closes the shared epistemic-graph transport.

The server itself is built by the agent-connector-sdk MCP factory, which owns
the authentication provider, network hardening, visibility filter, `/health`
and the privacy-safe error and per-caller rate-limit middleware. GraphOS adds
its host policy: every tool call is bound to the caller's verified actor and
graph session minted from the validated bearer claims (a call with neither
validated claims nor an ambient or `stdio` process authority is refused), and
`/metrics` is served on loopback, or remotely only behind the bearer resolved
from `MCP_METRICS_TOKEN_REF`. `/health/ready` reports ready or not ready only.

The fleet loader uses GraphOS's native multiplexer. Its EG-backed catalog read
is capability-gated on the generated kind-only `AgentComponent.Search` client
contract. When that authority is unavailable, catalog refresh fails closed; it
does not substitute a static reader. See [Capability status](status.md).

## Intent API cutover

The planned MCP cutover has **ten resident tools**: the six intent verbs `find`,
`ask`, `why`, `write`, `act`, and `manage`, plus `find_tools`, `load_tools`,
`unload_tools`, and `multiplexer_status`. Each verb accepts an exact `op` or a
natural-language `intent`, an object-valued `params`, optional `plan_ref` and
`idempotency_key`, and `execute`. `find(op="query.uql")` returns the operation's
typed schema. The operation registry also publishes `graphos://registry` and
`graphos://ops/{op_id}`; its digest lets clients refresh cached schemas.

For mutations selected from natural language, the first call returns a preview.
An exact operation still passes scope, policy, effect, and confirmation checks.
Destructive operations require a single-use plan; human administration and
approval require browser console confirmation with fresh MFA. These checks are
shared by MCP, HTTP, and A2A. See the [API contract](api.md) and
[fleet multiplexer](fleet-multiplexer.md).

The registry and projections are under development. The current release still
uses its existing MCP registration; [capability status](status.md) records the
cutover boundary. Clients should discover the served tool list at runtime.
