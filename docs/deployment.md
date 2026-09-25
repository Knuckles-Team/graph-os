# Deployment and configuration

GraphOS ships a local MCP server, an authenticated HTTP transport, configuration
and diagnostic tools, and guarded production operations. Choose a profile,
validate it, and run the release canary before changing live traffic.

## Install

GraphOS supports Python 3.12 through 3.14.

```bash
python -m pip install graph-os
```

Add the optional Agent WebUI host integration when the same process will serve
the UI:

```bash
python -m pip install "graph-os[webui]"
```

## Generate configuration

The configuration tool supports three deployment profiles:

| Profile | Intended use |
|---|---|
| `tiny` | Local evaluation and a minimal single-process environment |
| `single-node-prod` | A production host with externalized persistence and identity |
| `enterprise` | A managed multi-service deployment with production policy controls |

Generate and validate a profile:

```bash
setup-config generate --profile tiny
setup-config doctor --profile tiny
```

The default output follows the XDG configuration directories. Generated
configuration must contain secret references such as `vault://` or
`engine://__secrets__`; do not place plaintext credentials in configuration,
examples, command history, or source control.

Inspect the complete option reference without writing configuration:

```bash
setup-config reference
```

## Local MCP transport

Use `stdio` when an MCP client launches GraphOS as a child process:

```bash
graph-os --transport stdio
```

The helper can register that portable launcher with Codex:

```bash
setup-config codex
```

For other clients, configure `graph-os --transport stdio` using the client's
standard MCP server configuration format.

## Network transport

Serve the streamable HTTP transport on a private listener with a real token
verifier. This example uses the JWT boundary:

```bash
AUTH_JWT_AUDIENCE=graph-os KG_POLICY_VERSION=baseline-v1 \
  graph-os --transport streamable-http \
  --host 127.0.0.1 --port 8000 \
  --auth-type jwt \
  --token-jwks-uri https://identity.example/.well-known/jwks.json \
  --token-issuer https://identity.example/ \
  --token-audience graph-os
```

Network serving is a security boundary. Configure validated identity, tenant
isolation, TLS, and the deployment's authorization policy before exposing the
listener beyond loopback. GraphOS refuses network serving without a constructed
authentication provider and rejects required authority that is absent or
invalid.

## Validate a candidate

Run diagnostics against the resolved deployment configuration, then execute the
bounded release canary in the candidate environment:

```bash
agent-utilities-doctor
graph-os-release-canary
```

The canary reports aggregate readiness checks and does not print paths,
credentials, identities, or graph contents. A failed canary is a release
failure, not a warning to suppress.

The host daemon can be inspected independently:

```bash
graph-os-daemon --status
```

## Production operations

`graph-os-production-ops` provides guarded backup and restore-validation
commands. They require the deployment's workload identity, graph coordinator,
encryption, and mounted storage policy. Review the command help in the exact
candidate environment before use:

```bash
graph-os-production-ops --help
```

Production operations emit opaque digests and aggregate counts rather than
endpoints, principals, storage paths, credentials, or graph contents. Backup
and restore validation are operational changes; run them through the normal
change-control and recovery procedure for the target deployment.

## EG control-lease kinds for the graph-os identity

The graph-os service identity holds `lease:read` and `lease:write`. EG then
narrows it, per principal, to the control-lease kinds graph-os writes under
its own process identity (operator ruling 2026-09-24). Set this on the
epistemic-graph deployment:

```bash
EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON='{"<graph-os agent_id>": ["action.approval", "finance.order-proposal", "finance.paper-order"]}'
```

Any other kind that graph-os tries to issue or transition under its own
identity is refused with `ACCESS_DENIED`, including `rbac.elevation`.

An EG write is signed by the task-local verified session. The MCP middleware
(`graph_os/mcp_server/serving.py`) binds the caller's session for each
request. The process session is ambient everywhere else: server bootstrap
(`mcp_server/server.py`), the host daemon's autonomous loops
(`gateway/daemon.py`, `mint_process_identity`), and the finance executor
(`mcp_server/background.py`, `process_authority`).

This table covers every control-lease kind that graph-os-hosted code writes:

| Kind | Written by | Signing identity | Evidence |
|---|---|---|---|
| `finance.order-proposal` | `graph_os/finance/orders.py` `propose_order` (issue) | graph-os process | runs on `FinanceService`, which uses `process_authority`. The approve and deny transitions run under the approver's own claims (`gateway/finance_orders.py`). |
| `finance.paper-order` | `graph_os/finance/paper_orders.py` `submit` (issue) | graph-os process | The shared invoke service executor first checks the verified caller's `finance:paper-trade` scope and tenant graph read authority; its EG lease reserves a caller-owned request key before one paper-only Emerald call. Retry after a collision or unknown outcome is indeterminate and never resubmits. |
| `action.approval` | agent-utilities `orchestration/action_policy.py` `queue_approval` (issue). Drains to `expired` in `orchestration/fleet_reconciler.py` and `knowledge_graph/research/change_publisher.py`. | graph-os process, and callers | queued by the autonomous loops (fleet reconciler, auto-merge, remediation playbooks, guardrail evolution, spec proposals) under the process session. A tool call queues under its caller, and a console decision (`orchestration/approval.py`) runs under the approver. |
| `browser.control` | `graph_os/browser_control/browser_control_durability.py` | caller (the human) | the browser channel requires the ambient session to be the binding's verified human (`browser_control_service.py` `_validate_ambient_binding`). WebUI helpers run in the request's copied context. |
| `browser.registration`, `browser.document` | `browser_control/browser_control_registration.py` | caller (the human) | same channel and session as `browser.control` |
| `browser.attended_arm`, `browser.recent_auth` | `browser_control/browser_control_attended.py` | caller (the human) | same channel and session as `browser.control` |

Two lease-shaped records are not EG control leases, so the policy does not
apply to them:

- `messaging_intake_lease` is a WorkItem claim
  (agent-utilities `messaging/intake_lease.py`).
- Capacity leases use `AcquireCapacity` under the `capacity:*` scopes.

`graph_os/lease_kinds.py` records this classification.
`tests/test_lease_kinds.py` scans graph-os and the agent-utilities code it
hosts for control-lease issue sites. It fails when a kind is unclassified, and
when the allowlist above differs from the process-identity kinds.

## Release model

The release workflow builds the wheel after the quality and scanner jobs pass.
PyPI publication runs only for an explicit version tag and uses the protected
`pypi-publish` environment. A push to `main` updates source and documentation;
it does not publish a package.

See [Capability status](status.md) before deployment. Capability-gated paths
remain unavailable whenever their required authority is absent or unverified.
