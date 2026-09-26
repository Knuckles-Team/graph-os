# Operation API

GraphOS is building one operation registry for MCP, HTTP, and A2A. Each operation
has a stable ID, typed parameter and result schemas, an intent verb, exact
scopes, an effect class, and a confirmation rule. One invocation path performs
authentication, principal and scope checks, Eunomia policy, effect gating, and
audit before it reaches the owning engine or service.

The generated API is **not yet mounted in the current release**. The contract
below describes the cutover being implemented; use the currently served routes
until [capability status](status.md) reports the cutover complete.

| Planned HTTP endpoint | Purpose |
|---|---|
| `GET /api/v1/registry` | Discover operations visible to this caller; ETag is the registry digest. |
| `GET /api/v1/ops/{op_id}` | Read one visible operation's typed schema. |
| `GET /api/v1/openapi.json` | Read the generated OpenAPI document. |
| `POST /api/v1/ops/{op_id}` | Invoke an exact operation with JSON object parameters. |

An operation may also declare a resource route, such as an identity-user or
finance-alert route. Those routes use the same invocation path and result
envelope. Discovery omits operations the caller cannot use. A caller with
`mcp:admin` may request denied-item diagnostics separately.

Read effects execute directly. A natural-language selection of a mutation
returns a preview. A destructive operation always requires a plan bound to the
operation, parameter digest, caller, tenant, policy revision, and registry
digest. HTTP returns the preview as `428 Precondition Required` with a
`plan_ref`; the client resubmits with `Graphos-Plan-Ref`. Human administration
and approval return `STEP_UP_REQUIRED` and complete through the browser console
with fresh MFA. A denied or unavailable policy decision never grants access.

See [MCP server](mcp-server.md) for the intent verbs and
[fleet multiplexer](fleet-multiplexer.md) for dynamic child capabilities.
