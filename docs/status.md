# Status

**Partial Migration Wave 5 extraction (RF-ADR-009).** The host-facing deployment
surface now lives in `graph_os.deployment`: profile/config generation, preflight
and doctor checks, release canary, production backup/restore validation, venv
reconciliation, and deterministic backend plans. The REST gateway, control
plane, fleet catalog adapter, and WebUI host are also populated here. The live
service still starts the AU MCP/composition entrypoints in `services/graph-os`
(see `inventory/k8s-migration/GRAPHOS-LOCAL-REDEPLOY.md` in the workspace for
the current deployment).

| Package | Owns (RF-ADR-009 §2/§2.4) | Populated? |
|---|---|---|
| `graph_os.mcp_server` | the graph-os MCP tool surface | No — placeholder |
| `graph_os.fleet` | the fleet gateway / multiplexer | Partial — EG catalog adapter extracted; multiplexer pending |
| `graph_os.gateway` | REST gateway + host daemon | Yes — extracted; cutover pending |
| `graph_os.control_plane` | fleet reconciliation / action policy | Yes — extracted; AU deletion pending |
| `graph_os.webui_host` | agent-webui hosting | Yes — extracted; composition cutover pending |
| `graph_os.deployment` | host deployment, doctor, release canary, production ops | **Yes — extracted** |

The gateway's action/engine behavior is supplied by an explicit application
port. Phase G2 must configure that port from the graph-os MCP/fleet composition
before mounting routes; request handlers do not import AU's MCP implementation.
The `graph-os` console script remains a non-serving shell until the MCP lane
lands. Deployment scripts point directly to `graph_os.deployment`; AU release,
certification, analytics, and connector-certification tooling stay with their
owning repositories.

Remaining source this repository will receive in **Migration Wave 5** (line
counts measured 2026-09-12 against `agent-utilities` `HEAD`) is tracked in
[`AGENTS.md`](https://github.com/Knuckles-Team/graph-os/blob/main/AGENTS.md#w5-source-measurements-measured-2026-09-12)
"W5 source measurements".
