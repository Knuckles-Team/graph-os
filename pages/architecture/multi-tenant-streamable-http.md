# Multi-Tenant graph-os over Streamable-HTTP

Serving `graph-os` as a **streamable-HTTP MCP surface for thousands of clients**:
hierarchical **org → user** isolation, **private-by-default** memory with an
explicit **commons / markings** sharing path, full **tenant-stamped audit**, and an
**elastic per-tenant engine pool** — all opt-in, so single-tenant/local behaviour
is byte-for-byte unchanged when the flags are off.

Concepts: **OS-5.14** (served identity), **AU-KG.sharding.tenant-partitioned-sharding-hrw** (tenant→named-graph→shard),
**AU-KG.compute.data-is-private-its** (org→user sharing + commons), **AU-KG.backend.concept-2** (Postgres RLS), **AU-KG.sharding.elastic-over-kg-shard**
(engine pool), **AU-OS.safety.ontological-guardrail/5.11** (tenant-scoped fleet + audit). See also
[engine sharding](engine-sharding.md),
[company_brain_runtime](https://knuckles-team.github.io/agent-utilities/architecture/company_brain_runtime/),
[state_externalization](https://knuckles-team.github.io/agent-utilities/architecture/state_externalization/).

---

## Topology

One image (`graph-os`), three stateless tiers + central durable state. The cloud
(k8s) and homelab (Swarm) profiles differ only in replica counts and placement —
see [`deploy/`](https://github.com/Knuckles-Team/agent-utilities/blob/main/deploy/README.md).

<div class="admonition architecture" markdown>
<p class="admonition-title">Topology: three stateless tiers + central durable state</p>

Keycloak/OIDC mints a JWT (`org_id→tenant_id`, `sub→actor_id`) for clients,
which call in with a bearer JWT through a Load Balancer/Ingress to the
FRONT TIER (stateless streamable-HTTP + gateway, `KG_DAEMON_ROLE=client`).
The front tier resolves an `ActorContext` (tenant_id, actor_id, roles) and
hands it to the Tenant Router (engine `PlacementRoute` + bounded warm
views), which routes to one of N engine shards (tenant graphs, role=host)
or to the COMMONS engine (shared default graph, read-mostly). All three
fan out to an optional Postgres/pg-age mirror (write-only, RLS by
tenant_id). The front tier also writes directly to `STATE_DB_URI`
(sessions, turns, fleet, queue delivery) — the same central durable-state
tier the mirror feeds.

</div>

## The five isolation layers (defense in depth)

<div class="admonition architecture" markdown>
<p class="admonition-title">Five isolation layers, defense in depth</p>

1 · Identity (OS-5.14 served JWT) → 2 · Physical (named graph per org) →
3 · Logical (tenant scope + owner/scope) → 4 · Database (Postgres RLS
`app.tenant_id`) → 5 · Audit (tenant+actor stamped). Each layer is
independently enforced; a bypass at one layer is still caught by the next.

</div>

1. **Identity (OS-5.14).** `ActorIdentityMiddleware` mints `ActorContext{tenant_id,
   actor_id, roles}` from a validated JWT (`org_id→tenant_id`, `sub→actor_id`). The
   **served-security profile** (`apply_served_security_profile`) refuses to serve a
   network transport without a JWT validator, audience, and policy revision
   (fail-loud, not fail-open); unauthenticated HTTP is rejected and no implicit
   identity exists.
2. **Physical (AU-KG.sharding.tenant-partitioned-sharding-hrw + AU-KG.compute.data-is-private-its).** Each org routes to its own
   named graph `tenant__<slug>__<base>` — **even on a single engine endpoint**.
   The engine catalog owns physical placement; cross-org data is separate.
3. **Logical (KG-2.6 + AU-KG.compute.data-is-private-its).** On a shared graph, `scope()` injects
   `n.tenant_id = <org>` (the simple, parseable predicate) and a Python-side
   `visible()` filter applies private-by-default owner/scope. Applied at the
   `query_cypher` MCP read chokepoint and `facade.query`.
4. **Database (AU-KG.backend.concept-2).** Postgres Row-Level Security keyed on the per-session GUC
   `app.tenant_id` filters rows beneath everything else; `WITH CHECK` blocks
   cross-tenant writes. Apply [`deploy/postgres/tenant_rls.sql`](https://github.com/Knuckles-Team/agent-utilities/blob/main/deploy/postgres/tenant_rls.sql).
5. **Audit (AU-OS.safety.ontological-guardrail/5.11).** Every `RunTrace`, session, and correlation carrier is
   stamped `tenant_id`+`actor_id`+`correlation_id`; `/api/fleet/*` is tenant-scoped
   (an org admin sees its own org; a platform admin sees the fleet).

## Hierarchical org → user + commons sharing (AU-KG.compute.data-is-private-its)

The **default graph is the commons.** Data is **private to its owner by default**;
sharing is explicit — by **where** it is placed (promote into the commons graph) or
by **how** it is placed (a mandatory marking).

<div class="admonition architecture" markdown>
<p class="admonition-title">Private by default, explicit sharing</p>

A guarded write stamps `tenant_id`, `_owner_id`, `_shared_scope=private`,
starting private to its owner. From there, `graph_share action=org`
promotes it to org-shared (visible to the org); `action=commons` promotes
it to the cross-org-readable commons graph; `action=mark` applies a
role-gated, cross-org marking. `action=private` on an org-shared record
demotes it back to private.

</div>

A reader sees: **own** (`_owner_id == me`) ∪ **org/commons-shared**
(`_shared_scope ∈ {org, commons}`) ∪ **unowned** (legacy/system) ∪ the **commons
graph**. A verified `admin` is unrestricted by owner/scope visibility, while
tenant, session, and ACL boundaries remain mandatory.

Verbs (MCP tool `graph_share` / `POST /graph/share`):

| action | effect | mechanism |
|---|---|---|
| `org` | visible to the owner's org | in-place `_shared_scope='org'` |
| `commons` | cross-org readable | copy node into the commons graph |
| `mark` | role-gated cross-org | mandatory marking (AU-KG.ontology.redact-object-materialize-restricted) |
| `private` | restrict back to owner | `_shared_scope='private'` |

## Per-tenant graph views (AU-KG.sharding.elastic-over-kg-shard)

`GRAPH_SERVICE_ENDPOINTS` declares coordinator contacts. A process owns one engine transport;
tenant graphs are non-owning, session-routed views over that transport. A bounded
LRU may retain those lightweight views, but a miss or eviction never opens or closes
a socket/event-loop thread. Engine-side graph residency remains an engine capacity
decision rather than a client-lifecycle side effect.

## Configuration

| Flag | Default | Purpose |
|---|---|---|
| `AUTH_JWT_JWKS_URI` / `_ISSUER` / `_AUDIENCE` | — | OIDC identity; **required** for every network transport |
| `KG_POLICY_VERSION` | — | required immutable policy revision for verified sessions |
| *(baked-in, no flag)* graph authority | mandatory | verified session + tenant scope + explicit ACL + owner/scope filtering; missing policy infrastructure fails closed |
| `KG_AUTH_TOKEN_REF` / `KG_IDENTITY_OAUTH2` | — | exactly one stdio identity source: provisioned-token reference or OAuth2 client credentials |
| `KG_DEFAULT_GRAPH` | `__bus__` | the commons graph; tenants route to `tenant__<slug>__<this>` |
| `GRAPH_SERVICE_ENDPOINTS` | one socket | stable authenticated engine coordinator/bootstrap contact; placement is resolved from the engine catalog and verified `ClusterMembers` snapshot |
| `GRAPH_CLUSTER_ID` / discovery bounds | unset / `30s` / `5s` | optional pinned cluster identity plus last-good/certificate freshness bounds; stale or wrong-context discovery fails closed |
| `GRAPH_RAFT_GROUP_ENDPOINTS` | `{}` | compatibility/configuration-audit input only; live placed-group routing ignores this map and requires verified `ClusterMembers` authority |
| `GRAPH_DB_CONNECTION_PROFILE_REF` / `STATE_DB_URI` | — | Secret-backed pg-age mirror profile (apply RLS) / central session, fleet, and queue-delivery support store |
| `KG_ENGINE_POOL_SIZE` | `8` | bounded LRU warm set for retained graph views; it does not create per-tenant transports |
| `KG_ENGINE_POOL_DROP_ON_EVICT` | off | unload the tenant graph from the engine on eviction (needs a pg-age mirror) |

## Tracking clients & their agents

"Which agents did client X spawn?" is a tenant-scoped query: the run-wide
`correlation_id` (OS-5.11) links every spawned agent's `RunTrace`, each stamped
`tenant_id`/`actor_id`; `/api/fleet/*` filters by the caller's tenant. External
side-effects carry `x-tenant-id`/`x-actor-id`/`x-correlation-id` so off-box writes
remain joinable to the originating client.

## Verification

Unit + integration: `tests/unit/knowledge_graph/test_tenant_sharing.py`,
`test_tenant_engine_pool.py`, `test_tenant_request_isolation.py`,
`test_fleet_supervisory.py`, `test_postgresql_backend.py`,
`tests/unit/core/test_request_identity.py`. Live: per-tenant named-graph isolation
verified against a running engine; Postgres RLS (isolation + commons + admin-bypass
+ `WITH CHECK`) verified against Postgres 16 with `deploy/postgres/tenant_rls.sql`.
