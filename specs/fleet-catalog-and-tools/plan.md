# Architecture and implementation plan

## Existing wiring to reuse

| Existing seam | Role in this design | Change boundary |
|---|---|---|
| `graph_os/fleet/catalog_reader.py` and `epistemic_adapter.py` | Typed fleet catalog reads and generated engine adapter | Extend one canonical item projection; preserve strict integrity failures |
| `graph_os/fleet/multiplexer.py` | Child transport, session visibility, mounts, meta-tool registration | Make it a transport/session component; route authority and effects through `invoke`; remove default-open branches |
| `graph_os/fleet/session_notifications.py` | Per-session list-change queue | Notify on load, unload, revocation, and generation change |
| `graph_os/fleet/child_resilience.py` | Child health and reconnect | Include generation and health in status without granting authority |
| `graph_os/mcp_server/{runtime,server,catalog_composition,composition}.py` | Served FastMCP instance and startup composition | Install the ten registry projections on the same event loop; fail startup on incomplete attachment |
| `graph_os/webui_host/mcp_delegation.py` | Host-to-MCP bridge | Route calls through the same registry/policy boundary |
| `graph_os/deployment/doctor.py` | Local environment and policy diagnostics | Report policy off, unavailable, invalid config, and catalog digest mismatch |

Build on these seams; do not introduce a second catalog authority, child-call path, or policy decision point. `graph_os/api/registry/` owns immutable operation metadata. `graph_os/api/invoke/` owns validation, caller binding, exact scopes, policy, effects, execution, audit, and error mapping. `graph_os/api/mcp/` projects intent and resident fleet tools. `graph_os/fleet/gateway_ops.py` adapts catalog operations and creates native forwarders whose body delegates to `invoke("fleet.call", ...)`. The engine contract provides method schemas/scopes and durable event APIs; the SDK provides certified connector manifests and pack annotations.

## Data and generation model

```text
verified principal + policy revision
    -> authorized registry view + fleet catalog snapshot
    -> generation {id, digest, typed items, source versions}
    -> MCP / HTTP / A2A discovery and session mounts
    -> invoke(op, params, caller, surface)
    -> delegated child or engine under verified caller
```

Use frozen typed models for `OpSpec`, `CatalogItem`, `CatalogGeneration`, `ReloadReceipt`, `SessionLoad`, and `OpError`. `OpSpec` includes ID, verb, summary/examples, params/result schema, binding, caller and executor scopes, subject, effect, principal rule, confirmation, surfaces, idempotency, stability, and audit class. Canonicalize sorted IDs and normalized schemas, then SHA-256 the serialized form. Version and digest must appear in MCP instructions, HTTP registry response, and A2A card. A curated alias suppresses the generated engine op so one engine method has one public identity.

The catalog join uses the verified **runtime MCP server name** from the child session, plus its tenant and source identity. Package ID and transport key remain separate metadata. Reject duplicate runtime names or an ambiguous alias. A connector schema pin is the canonical digest of its real `tools/list` schema; a failed or empty probe cannot silently certify it. Validate body digests for skills, prompts, resources, and templates before a candidate generation is eligible.

## Request and authority flow

1. Surface adapter supplies a `VerifiedCaller` from authenticated MCP, HTTP, or A2A context; local/stdio creates an explicit scoped bootstrap principal.
2. Resolve a registered op, forbid extra fields and caller-asserted authority, and validate a typed schema.
3. Check principal kind and exact caller scopes. `mcp:discover`, `mcp:delegate`, item scopes, and domain scopes have no cross-class admin implication.
4. Evaluate narrowing-only Eunomia at discovery, load, and every call. Enabled and unreachable yields `POLICY_UNAVAILABLE`; disabled still runs scope and principal filtering.
5. For service-executed work, check the caller's subject-read authority before entering the service identity; verify the service grant allowlist. For caller-executed work, use the verified caller context directly.
6. Apply READ/WRITE/DESTRUCTIVE/ADMIN effect rules and preview/plan/console confirmation. Require idempotency keys for side effects that can retry.
7. Dispatch with the existing timeout and interactive priority, append a minimal audit event, and map errors without changing engine error codes.

Discovery omits denied items. A privileged policy-debug response may identify the denying layer only with exact `mcp:admin` and an explicit request. Loaded native forwarders re-evaluate all checks on **each call**, even if a prior load succeeded. A scope or policy revision change removes visibility and queues notifications; a stale native call returns a stable denial.

## Reload state machine

| Phase | Required behavior |
|---|---|
| Snapshot | Read current active generation and source versions; acquire a single-writer generation token |
| Build | Fetch child catalog and bodies, certified pack entries, registry, and policy metadata off the serving path |
| Validate | Check schema/body digests, duplicates, deletion semantics, identity joins, scopes, and cross-surface projections |
| Prepare | Persist candidate and ordered delta events with one trace ID; do not expose candidate to readers |
| Publish | Atomically swap the active generation pointer after validation; each read sees one whole generation |
| Drain | Let calls pinned to the previous generation finish until a bounded deadline; cancel/return retryable outcome after it |
| Notify | Queue per-session tool/prompt/resource/skill list changes and durable ingestion events in source order |
| Reconcile | Replicas compare active ID/digest; lagging replicas remain last-known-good or report degraded, never assemble mixed generations |

On any build/validation/publish failure, retain the last-known-good active generation, record a typed failure receipt, and make no positive list-change claim. Deleted assets disappear only after a valid new generation publishes; stale loaded calls deny once deletion takes effect. A repeated reload of the same digest is idempotent and produces no duplicate ingestion. Every receipt carries prior/candidate/active IDs, digest, counts by kind, trace ID, status, and deadline.

## Sequence

1. Define registry and canonical digest; generate engine bindings from the packaged method/schema/scope contract and maintain explicit exclusions.
2. Add typed `invoke` and the fleet gateway adapter. Make existing child call sites delegate through it; prove no alternate path by static scan and tests.
3. Project six verbs, four resident fleet tools, typed discovery, generated clients, and API/A2A views. Use one filtered catalog and one resolver; remove legacy harvest and granular registration only after the replacement starts and serves.
4. Add strict policy modes, exact fleet scopes, session cap/expiry, native forwarders, notifications, and revocation handling.
5. Add candidate generation and reload state machine, durable delta receipts, replay, and multi-replica convergence checks.
6. Re-certify connectors against actual served schemas and wire pack annotations, write-back effect classes, feed sanitation, run admission, and assembly model parsing.
7. Run the gate matrix and publish exact evidence; only then move each slice through SOURCE LANDED to ACCEPTED.

## Development environment

A contributor starts with this repository and its declared lockfiles, installs local dependencies, runs generated-contract checks and fixture tests, and starts a local MCP server with fake engine/child adapters and bundled policy. The test suite must not require private DNS, secrets, an existing tenant, or a live production service. Mark unavailable optional integration tests separately and use a provisionable container/local fixture for CI. Release acceptance adds a disposable two-replica graph-os setup and synthetic connector; its setup commands and manifests belong in this repository.
