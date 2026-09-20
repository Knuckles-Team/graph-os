# Status

**Migration Wave 5 extraction in progress.** The REST gateway and gateway host
daemon are populated and tested here. The live deployment still starts the AU
entrypoints until the coordinated consumer cutover in `services/graph-os`
(see `inventory/k8s-migration/GRAPHOS-LOCAL-REDEPLOY.md` in the workspace for
the current deployment).

| Package | Owns (RF-ADR-009 §2/§2.4) | Populated? |
|---|---|---|
| `graph_os.mcp_server` | the graph-os MCP tool surface | No — placeholder |
| `graph_os.fleet` | the fleet gateway / multiplexer | No — placeholder |
| `graph_os.gateway` | REST gateway + host daemon | Yes — extracted; cutover pending |
| `graph_os.control_plane` | fleet reconciliation / action policy | No — placeholder |
| `graph_os.webui_host` | agent-webui hosting | No — placeholder |
| `graph_os.deployment` | deployment doctor / release canary / prod ops | No — placeholder |

The gateway's action/engine behavior is supplied by an explicit application
port. Phase G2 must configure that port from the graph-os MCP/fleet composition
before mounting routes; request handlers do not import AU's MCP implementation.

Source this repository will receive in **Migration Wave 5** (line counts
measured 2026-09-12 against `agent-utilities` `HEAD`) is tracked in
[`AGENTS.md`](https://github.com/Knuckles-Team/graph-os/blob/main/AGENTS.md#w5-source-measurements-measured-2026-09-12)
"W5 source measurements".
