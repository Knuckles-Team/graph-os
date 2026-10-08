# Verification contract

Every assertion is run against an exact commit and reports a pass/fail result. Fixture tests are required for a clean checkout and cloud PR; served release probes are separately recorded before ACCEPTED. Do not turn an unavailable external environment into a blocking source hook.

## Positive and negative cases

| Area | Positive case | Negative case and required outcome |
|---|---|---|
| Registry | Every packaged wire-callable engine method has one op or a reviewed exclusion; digest repeatable across two builds | Missing method, stale exclusion, schema or generated artifact drift fails a local gate |
| Resident MCP | Exactly six verbs and four fleet tools appear at boot with correct schemas; FastMCP 4 protocol clients call them | Missing registration fails startup; retired granular/harvest tool cannot reappear |
| Discovery | Authorized tool, prompt, resource/template, skill, and connector item appear with correct body/schema and same result set across MCP/HTTP/A2A | Unauthorized, stale, duplicate, malformed, or digest-mismatched item is absent or rejects refresh; no partial catalog |
| Resolver | First example for each op ranks it top three; tenant and policy partition feedback | Cross-tenant reward/cache leak rejected; fuzzy match cannot execute a write |
| Load and lifecycle | Native schema matches child, session isolation, list-change and fallback work; one-hour idle expiration and cap behavior | 65th load without explicit eviction returns `LOAD_CAP_EXCEEDED`; unknown native name denies; evicted or expired name cannot call |
| Scopes and policy | Exact `mcp:discover`/`mcp:delegate` plus item scopes allow matching caller; local principal has explicit scopes | `admin`/`kg:admin` alone, unscoped stdio, missing child scope, PDP denial or outage all deny; policy off stays scope filtered |
| Revocation | A loaded call passes, then scope/policy revision changes; next call rechecks and notification is queued | Revoked native forwarder, `fleet.call`, HTTP, and A2A must all deny with the same category; no stale-cache grace |
| Effects | READ executes; WRITE requires appropriate plan/idempotency; destructive requires confirmation; admin requires console step-up | Caller-supplied actor/tenant rejected; service child without domain scope/subject read rejected; replay key conflict rejected |
| Child identity | Distinct package, transport, and runtime MCP names resolve to verified runtime name | Duplicate runtime names and ambiguous aliases reject candidate; static service token cannot be used as caller token |
| Connector pins | Real `tools/list` schema fingerprint matches certified manifest and pack annotations | Empty placeholder schema, changed schema, or missing effect annotation cannot be certified or silently treated as READ |
| Reload | Candidate validates, publishes one generation atomically, sends ordered deltas, re-ingests changed bodies once | Malformed/duplicate/stale/deleted/digest-mismatch candidate retains last-known-good; in-flight drain timeout bounded; no mixed replica generation |
| Assembly/admission | Real generated model's complete `agents` list survives parsing; capacity acquired all-or-nothing and released on stop | Dict fakes, partial capacity lease, second re-decision, dropped agent, or leaked lease fail |
| Catalog admission | The reader skips a bare registration; joined servers serve; status lists the skipped name | Receipt, revision, pin, content and page faults still abort the read |
| Fleet onboarding | The pass registers, attests and imports each configured server; the CLI reports each server | The pass reports a failing server, a registry outage or a missing credential; other servers and boot continue |
| Lease renewal | A lease that lapses within six hours renews under a new window key; self-served leases renew | The pass leaves a fresh lease alone; a refused catalog refresh keeps the prior catalog |
| Feeds | Tenant-bound telemetry/security/CI metadata enters catalog and audit through typed ops | Raw secret values, unsanitized identity/IP, cross-tenant events and live trading without write-back authorization reject |

## Authority parity oracle

Table-drive principal kind (human, delegated human, service), tenant, exact scopes, Eunomia mode and decision, effect class, item kind, and surface. Invoke an op through an MCP verb, resident fleet tool, loaded native forwarder, HTTP, and A2A. For equivalent operations assert the same allow/deny and stable error code, including unknown op, missing scope, principal mismatch, PDP down, confirmation required, step-up required, and child failure. Discovery views for each principal must produce the same authorized identities. Engine error codes are preserved verbatim in the envelope; fleet errors preserve the child code and mark the source.

The oracle must include a loaded tool followed by scope removal, policy revision, item deletion, and server identity change. The next call, even by native name without a new `tools/list`, must refuse. Verify audit fields are the same for native forwarder and `act op=fleet.call`; the audit stores only parameter digest, not secret values.

## Reload and served probe

Provision two local graph-os replicas, one synthetic child MCP server, a fixture policy, and a disposable durable event store. Capture the callable reload op ID, prior/candidate/active generation IDs and digests, one shared trace ID, and timestamps. Change one tool schema, prompt body, resource/template body, and skill body; refresh; prove the two replicas converge on the same complete generation, each affected item has one ordered ingestion receipt, and sessions receive the matching notifications. Hold one call in flight during swap to prove drain behavior. Repeat with a malformed body, duplicate server identity, stale digest, deleted asset, and policy outage; record last-known-good, error, and absence of partial publication. Restart one replica and replay receipts to prove durability and idempotency.

CI may run this probe with local containers/processes and synthetic credentials. A deployment-specific probe may additionally validate a real cluster, but its absence does not make unrelated cloud PR hooks fail. Never claim ACCEPTED until the served probe passes against landed code.

## Quality and contribution gates

- Run repository format/lint/type/unit suites, packaged contract regeneration in `--check` mode, and targeted MCP protocol tests from a clean checkout. Pin tools and fixtures in repository manifests; avoid host-only paths and ambient credentials.
- CCCC: new or changed functions meet the repository's cyclomatic/ABC limits (target at most 10, absolute 15); split orchestration into small typed steps. KISS limits: at most 35 statements, 8 parameters, 20 locals, depth 5, 8 returns, 1 boolean parameter, 900 lines/file, 40 functions/file unless generated declarations require a documented different check. No skip, xfail, noqa, or type-ignore is used to hide a failure.
- jscpd: zero new duplicate pairs over merge base. Dupehound: zero new clones. Reuse the existing catalog reader, multiplexer, notification, and served composition paths rather than copying them.
- Static gates check no unregistered surface, no child transport call outside the fleet gateway, all native forwarders call `invoke`, no default-open unknown tool path, exact scope and service-grant registry, and generated artifact parity. Runtime gate checks policy mode matrix and authority parity.
- Fail a PR only for reproducible source, contract, or fixture failures. Record optional remote dependency or live-environment observations as release evidence, with a provisionable substitute for contributor CI.

## Evidence record template

| Commit | Case/gate | Command or fixture | Result | Environment | Timestamp | Receipt or trace |
|---|---|---|---|---|---|---|
| pending | pending | pending | NOT RUN | clean checkout | pending | pending |
