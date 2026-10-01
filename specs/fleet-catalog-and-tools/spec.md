# Fleet catalog, intent tools, and policy safe reload

**Spec ID:** GRAPHOS-FLEET-001

**Owner:** graph-os

**State:** READY FOR IMPLEMENTATION — architecture and acceptance specified; no claim that the full surface is deployed or accepted.
**Scope IDs:** GRAPHOS-FLEET-R001, GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R003, GRAPHOS-FLEET-R004, GRAPHOS-FLEET-R005, GRAPHOS-FLEET-R006, GRAPHOS-FLEET-R007, GRAPHOS-FLEET-R008, GRAPHOS-FLEET-R009, GRAPHOS-FLEET-R010, GRAPHOS-FLEET-R011, GRAPHOS-FLEET-R012, GRAPHOS-FLEET-R013, GRAPHOS-FLEET-R014, GRAPHOS-FLEET-R015, GRAPHOS-FLEET-R016, GRAPHOS-FLEET-R017, GRAPHOS-FLEET-R018, GRAPHOS-FLEET-R019, GRAPHOS-FLEET-R020, GRAPHOS-FLEET-R021, GRAPHOS-FLEET-R022, PA-12.

## Outcome and state legend

An authenticated caller can discover only usable graph-os operations and fleet items, load a bounded subset as native MCP tools, execute every action through the same typed authority and audit path, and refresh the catalog without a partial generation becoming visible. An external contributor can build and test this from a clean checkout using fixtures and local processes; a live shared environment is an optional release proof, not a prerequisite for source contribution.

| State | Meaning | Required evidence |
|---|---|---|
| DRAFT | Requirements or design unresolved | Named open decision |
| READY FOR IMPLEMENTATION | Complete user, architecture, interface, and test contract | This four-file spec reviewed |
| BUILDING | Code or tests in progress | Linked branch or PR |
| SOURCE LANDED | Reviewed code on the repository default branch | Merge commit and local/CI gates |
| ACCEPTED | All listed functional and release proofs pass against the landed revision | Evidence with revision, environment, and result |
| BLOCKED | A named dependency prevents the next acceptance step | Dependency, owner, and retry condition |

Status is per deliverable; a source commit, a green unit test, or a prior status label never implies ACCEPTED. Update the evidence table in this spec at each transition.

## Functional contract

1. **One registry.** The operation registry owns stable IDs, typed input/output, effect, exact caller scopes, principal rules, executor, confirmation, audit class, permitted surfaces, and binding. Every wire-callable engine method is covered by exactly one generated or curated operation, or by a reviewed exclusion with a reason. A deterministic canonical registry digest identifies the active generation. No public tool, HTTP action, or A2A operation bypasses the registry.
2. **Ten resident MCP tools.** The six intent verbs are `find`, `ask`, `why`, `write`, `act`, and `manage`; the four fleet tools are `find_tools`, `load_tools`, `unload_tools`, and `multiplexer_status`. The server fails startup if any resident tool is missing. `find_tools` with `browse=true` replaces a separate `list_catalog`. Old skill/prompt harvesting and granular graph-os tool registration are removed after the replacement is served. FastMCP 4 capabilities used for native tools, prompts, resources, templates, and Skills-over-MCP must be verified by local protocol tests.
3. **One filtered catalog.** Search covers fleet tools, prompts, resources, resource templates, agent skills, and connector pack items. Each item has stable identity, kind, source server or pack, schema/body digest, required scopes, effect, generation, and availability. `find` and `find_tools` rank the same authorized set; the HTTP registry/catalog and A2A discovery expose equivalent authorized items. A query may include kind/server filters, pagination, and a context budget. Natural language resolution uses generated descriptors, tenant/policy-partitioned outcome feedback, a bounded cache, and preview for mutations; it cannot authorize a mutation by ranking alone.
4. **Session loading.** `load_tools` mounts native forwarders with the child input schema and sends list-change notifications. Loaded items are session-scoped; default cap is 64, hard cap 256; default idle expiration is one hour. Over-cap loading returns `LOAD_CAP_EXCEEDED` with loaded-item details or uses explicit least-recently-used eviction. Unload can target items, server, kind, or all. A result includes a callable name, schema, and `act`/`fleet.call` fallback for clients that ignore notifications. Connector items return their typed operation instead of a fake native tool.
5. **Exact authority on every path.** Discovery, load, and call each require an authenticated principal, exact `mcp:discover` or `mcp:delegate` scope as applicable, item scopes, principal rules, and a narrowing-only Eunomia decision. `admin` and `kg:admin` do not imply fleet scopes; stdio and local modes still have a real scoped principal. Unknown tools deny by default. With Eunomia disabled, exact scope filtering remains. With Eunomia enabled but unavailable, the operation fails closed. Policy revision or scope revocation invalidates loaded visibility before the next call and emits list-change.
6. **One invocation chokepoint.** Native forwarders, intent verbs, HTTP, and A2A call the same `invoke` pipeline. Child calls use the caller's delegated credential. A service-credential child requires a declared domain scope, caller subject-read check, owner-stamped audit, and an explicit service executor. Unannotated child effects default to WRITE; `readOnlyHint` is READ, `destructiveHint` requires a preview/plan confirmation, and admin-class effects require console step-up. Caller-supplied authority fields are rejected. Engine error codes are preserved in a stable envelope.
7. **Atomic catalog refresh.** The callable registry operation `fleet.catalog.reload` reads and validates a candidate catalog, all referenced bodies and schemas, server identity bindings, and policy metadata. Its typed request is `{expected_active_digest?: string, dry_run: bool = false, idempotency_key: string}`; only a caller with exact `fleet:control` and `mcp:admin` may publish. Its result includes prior/candidate/active generation IDs and digests, changed counts by kind, drain deadline, trace ID, and outcome. Duplicate, malformed, stale, missing, deleted, or digest-mismatched assets cannot publish a partial generation. It publishes a complete new generation atomically, retains the last known good generation on failure, drains in-flight calls within a bounded deadline, and emits ordered delta receipts for changed tools, prompts, resources/templates, and skills. A shared trace ID connects request, generation swap, notifications, and durable re-ingestion. Multi-replica convergence is observed by generation/digest; a replica that cannot reach the active generation reports degraded and does not serve a mixed catalog.
8. **Connector/pack integration.** Connector SDK manifests and verified runtime MCP server identities are used as inputs; package names, transport keys, and MCP runtime names are distinct fields. Tool schema fingerprints are computed from actual served schemas, never empty placeholders. Pack annotations carry capability, schema digest, modality, cost, latency, and effect. SQL/market-data and telemetry connectors are catalog items only after certification and policy checks. D18 write-back is a typed, previewed, idempotent operation; it must not be exposed as an unguarded child tool.
9. **Fleet operations.** Agent/A2A, browser, run admission, and fleet calls are typed operations. Capacity admission is all-or-nothing; a denied acquisition may trigger one re-decision, and a stopped run releases capacity. Assembly consumes the real generated `agents` list and never silently drops entries. Telemetry, security-audit, and CI event feeds expose only approved, sanitized data through the same registry and tenant boundary.

## Delivery slices and ownership

| Slice | IDs | Acceptance boundary |
|---|---|---|
| Registry and generated binding | GRAPHOS-FLEET-R011, GRAPHOS-FLEET-R012, GRAPHOS-FLEET-R016 | Exact engine coverage, stable digest, artifact drift checks |
| Intent and discovery | GRAPHOS-FLEET-R013, GRAPHOS-FLEET-R014, GRAPHOS-FLEET-R005, GRAPHOS-FLEET-R006, GRAPHOS-FLEET-R007 | Ten resident tools, typed schemas, no legacy harvest, FastMCP 4 protocol proof |
| Connector and fleet item inputs | GRAPHOS-FLEET-R001, GRAPHOS-FLEET-R002, GRAPHOS-FLEET-R003, GRAPHOS-FLEET-R004, GRAPHOS-FLEET-R008 | Real schema pins, pack annotations, typed write-back, sanitized feed metadata |
| Fleet invocation and safety | GRAPHOS-FLEET-R015, GRAPHOS-FLEET-R018, GRAPHOS-FLEET-R019, GRAPHOS-FLEET-R020, GRAPHOS-FLEET-R021 | Native load/call, exact scopes, policy, no bypass, parity |
| Orchestration correctness | GRAPHOS-FLEET-R009, GRAPHOS-FLEET-R010 | Atomic admission, correct generated assembly agents |
| Reload and acceptance | GRAPHOS-FLEET-R022, PA-12, GRAPHOS-FLEET-R017 | Atomic generation swap, re-ingestion, negative cases, replica/served proof |

The engine owns its durable records and method contract; the connector SDK owns pack and connector certification; graph-os owns serving composition, registry projection, fleet policy, session loading, and reload. A contributor may replace a missing external service with the fixtures and local fake adapters described in [test-spec.md](test-spec.md).

## Acceptance and evidence

Acceptance requires every functional rule above, the positive/negative matrix in [test-spec.md](test-spec.md), a clean checkout run, generated artifact parity, no new duplication, and a served multi-session/multi-replica proof for reload and authority. Proof records must name the exact commit, command, fixture or endpoint, result, timestamp, and any environment limitation. Release-only probes may run after source lands; leave state at SOURCE LANDED until they pass. The HTML spec report may render this table directly; Markdown remains authoritative.

| Slice | State | Evidence |
|---|---|---|
| Registry and generated binding | READY FOR IMPLEMENTATION | Pending exact revision and gate results |
| Intent and discovery | READY FOR IMPLEMENTATION | Pending exact revision and gate results |
| Connector and fleet item inputs | READY FOR IMPLEMENTATION | Pending exact revision and gate results |
| Fleet invocation and safety | READY FOR IMPLEMENTATION | Pending exact revision and gate results |
| Orchestration correctness | READY FOR IMPLEMENTATION | Pending exact revision and gate results |
| Reload and acceptance | READY FOR IMPLEMENTATION | Pending exact revision and gate results |

## Fixed design decisions

- `fleet.catalog.reload` is the public control ID and its request/result fields are specified above. Dry-run validates and reports a candidate without swapping the active pointer.
- Use the engine's generated durable event contract for catalog delta receipts and replay; graph-os supplies a local fixture adapter for contributor tests. Event keys include tenant, generation, sequence, and item ID to make replays idempotent.
- Ship a local embedded policy and allow/deny fixtures in this repository so a fresh checkout exercises policy without a remote PDP.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
