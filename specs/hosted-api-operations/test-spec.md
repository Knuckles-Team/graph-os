# Hosted API and intent operations — test contract

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. Governing [spec](spec.md).

| Test | Requirements | Setup and observation |
|---|---|---|
| HO-T01 | HO-01, HO-07 | From a pinned public engine wheel enumerate `is_wire_callable` methods. Compare with curated/generated op bindings and explicit live exclusions; fail on omissions, stale exclusions, duplicate ownership, or an unregistered GraphOS route/tool. Regenerate artifacts twice and compare bytes/digest. |
| HO-T02 | HO-02 | Table-drive human, service, delegated agent, tenants, exact scopes, and surfaces through `invoke`. Assert same allow/deny code on MCP, HTTP and A2A; extra fields and identity/tenant spoofing fail before a handler call. |
| HO-T03 | HO-02, HO-08 | A service op with domain scope but no caller subject read fails; subject-read plus service grant succeeds and creates a caller-owned record. Revoke either permission before deferred dispatch and assert zero effect. Cross-tenant subject always fails. |
| HO-T04 | HO-03 | Exact idempotent write replays one receipt; a missing key fails. Fuzzy mutation returns preview and creates no effect. Destructive plan is single-use and rejects expired, changed params, actor, tenant, policy revision or registry digest. Console-only admin rejects MCP/A2A confirmation and accepts same-human fresh-MFA console confirmation. |
| HO-T05 | HO-03, GRAPHOS-OPS-R036 | Make durable audit reservation fail: handler must not run. After effect, simulate audit outcome-link failure: return `INDETERMINATE`, retain reservation, and reconcile once. Assert no false rollback and no raw parameter value in durable audit. |
| HO-T06 | HO-04 | Discover each fleet item kind; load a native tool and prove `tools/list`, direct native call and `act fleet.call` reach identical `invoke` policy/audit. Revoke scope/PDP rule, assert next call denied, tool removed and notification sent/queued. Cap 64 and hard 256; explicit LRU; idle TTL; fallback usable when client ignores list-change. |
| HO-T07 | HO-05 | Generated HTTP route and MCP verb for one read and one effect reach the same service mock; bearer and cookie+CSRF variants; cursor paging; idempotency header; protocol routes inventoried; retired route absent only after generated consumer exists. |
| HO-T08 | HO-06 | Every published engine code maps without rewording; unknown engine code is a contract failure. Fleet code/target preserved. Error envelope is stable and privacy scrubbed. Registry digest appears on success/error, registry ETag, and MCP instructions. Stable-operation breaking diff fails compatibility check. |
| HO-T09 | HO-07 | Exercise one representative typed op from each listed domain through the generated client. Missing upstream dependency returns `UNAVAILABLE` and is absent from usable discovery. No domain handler directly opens an engine, agent or child transport outside its public port. |
| HO-T10 | HO-08 | Per identity mode and PDP state: policy-off retains scope filter; policy-on outage fails closed; admin scope never implies delegate; policy revision invalidates loaded items; destructive/admin decisions are uncached. Discovery sets match across MCP, HTTP and A2A for one principal. |
| HO-T11 | HO-09 | On clean public checkout, install pinned dependencies and run focused tests without private endpoints/sibling trees. In CI provision ephemeral local services, call served MCP and HTTP on the same request, and verify one durable result and audit pair. |
| HO-T12 | HO-10 | Generate the former-tool/route inventory; every name is mapped or has reviewed drop reason. Use the generated Python and TypeScript clients in a real caller test. Search published source for retired names and verify no active consumer uses an old route. |
| HO-T13 | HO-11 | Boot with one FastMCP loop and multiplexer. Enumerate tools/routes/methods and compare to registry plus explicit protocol list; remove any one resident registration in a fixture and assert startup fails. A loaded forwarder is the only runtime addition and its body calls `invoke`. |
| HO-T14 | GRAPHOS-OPS-R039 | Build the dashboard router. All 15 old method and path pairs present |
| HO-T15 | GRAPHOS-OPS-R039 | Read-only caller on mutating routes. 403; API-key caller passes; health open |
| HO-T16 | GRAPHOS-OPS-R040 | Create with empty template or oversize data. Typed validation or 400 refusal |
| HO-T17 | GRAPHOS-OPS-R040 | Create then get. Id and rendered output returned; same artifact read back |
| HO-T18 | GRAPHOS-OPS-R040 | Get unknown id. 404 |
| HO-T19 | GRAPHOS-OPS-R040 | Refresh with inline data, with resolver, and with a failing resolver. New render; resolver render; prior render preserved with ok false |
| HO-T20 | GRAPHOS-OPS-R040 | Mount on the host app; read-only caller POSTs. Routes present; 403 on POST |
| HO-T21 | GRAPHOS-OPS-R041 | Discover registry widgets. genius_agent absent |

## Drift and scanner acceptance

Run `uv run pytest tests/api tests/fleet tests/mcp_server tests/gateway`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os`, `uvx --from pre-commit==4.6.0 pre-commit run --all-files`, `uv build --wheel --out-dir dist`, and the repository's pinned scanner hooks. CCCC and KISS must stay within repository thresholds; jscpd adds zero clone pairs; Dupehound adds no new clones. Static checks reject transport calls outside the fleet executor, operation surfaces outside the registry, duplicate middleware authority, and frontend raw API bypass. Record exact command, tool versions, commit SHA, CI URL, and pass/fail in `evidence.md` before setting delivery to `ACCEPTED`; `evidence.md` is created when evidence exists. Never invent a pass from this spec.
