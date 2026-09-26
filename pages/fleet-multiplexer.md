# Dynamic fleet multiplexer

The approved MCP cutover retains four resident fleet tools alongside the six
intent verbs. `find_tools` discovers what the caller can use;
`find_tools(browse=true)` pages the catalog formerly exposed by `list_catalog`.
`load_tools(items=[...])` makes selected items native to the caller's MCP
session. `unload_tools` removes them, and `multiplexer_status` reports child
health, the loaded set, cap use, and notification delivery.

| Kind | Discovery source | Load result |
|---|---|---|
| `tool` | Verified engine catalog and live child probe | Native session tool named `<server>__<tool>`. |
| `prompt` | Catalog and child prompt list | Native MCP prompt. |
| `resource`, `resource_template` | Child resource lists | Native proxied resource or template. |
| `skill` | Catalogued skill body | Skills-over-MCP when supported; otherwise body and referenced operations. |
| `connector_item` | Connector SDK pack | The typed operation that uses the item. |

The planned default per-session load cap is 64 items, with a hard ceiling of
256. Resident and policy-filtered always-load items do not consume the cap.
Beyond the cap, a load returns `LOAD_CAP_EXCEEDED` unless `evict="lru"` is
requested. Idle session state expires after 3600 seconds by default.

Loading sends the applicable `list_changed` notification. The server queues a
retry for the next session request. Every loaded tool also returns its native
name, input schema, and an `act(op="fleet.call", params={...})` fallback for
clients that never refresh `tools/list`. Native calls and the fallback share
the same invocation checks. A tool can be called by native name after loading
even if the client missed the notification.

## Policy visibility

Exact scopes and principal rules apply before Eunomia. Eunomia can narrow
access at discovery, load, and call time; it cannot grant a scope. The planned
profile defaults are:

| Identity mode | `EUNOMIA_TYPE` default | If Eunomia is off |
|---|---|---|
| `none` (loopback tiny) | `none` | Bootstrap-principal scopes apply; policy-off banner. |
| `local` (single-node production) | `embedded` | Scope-filtered discovery; doctor warning. |
| `external` | `embedded` or `remote` | Scope-filtered discovery; doctor warning. |

If Eunomia is enabled but unavailable, discovery is empty with
`POLICY_UNAVAILABLE` and calls refuse. A changed policy revision removes
newly denied loaded items and sends `list_changed`. Approval and administration
still require console step-up even when policy allows them.

This behavior is a cutover contract, not a claim that the current release
serves all item kinds. See [capability status](status.md).

## Client configuration

Workspace `mcp_config.json` and `mcp_config_opencode.json` are client launch
inputs, not a second capability registry. Keep the configured GraphOS command,
transport, and authentication aligned with the deployment; discover operations
and fleet items from the running service. `MCP_TOOL_MODE` is not a GraphOS
setting after cutover. Connector children served on their own may still use
their own tool-mode setting. Do not put policy decisions or a fixed list of
fleet tool names in client launch files.
