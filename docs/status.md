# Status

**Pre-extraction scaffold (RF-ADR-009 W0 / W5-prep).** This repository exists and
is green, but it does not yet run graph-os — the live MCP server, REST gateway,
control plane, fleet gateway, and webui hosting all still run out of
`agent-utilities` and are deployed by `services/graph-os`
(see `inventory/k8s-migration/GRAPHOS-LOCAL-REDEPLOY.md` in the workspace for
the current deployment).

| Package | Owns (RF-ADR-009 §2/§2.4) | Populated? |
|---|---|---|
| `graph_os.mcp_server` | the graph-os MCP tool surface | No — placeholder |
| `graph_os.fleet` | the fleet gateway / multiplexer | No — placeholder |
| `graph_os.gateway` | the REST gateway | No — placeholder |
| `graph_os.control_plane` | fleet reconciliation / action policy | No — placeholder |
| `graph_os.webui_host` | agent-webui hosting | No — placeholder |
| `graph_os.deployment` | deployment doctor / release canary / prod ops | No — placeholder |

The one real, tested surface is the `graph-os` console script
(`graph_os.cli:main`), which reports the installed version and this
repository's target composition.

Source this repository will receive in **Migration Wave 5** (line counts
measured 2026-09-12 against `agent-utilities` `HEAD`) is tracked in
[`AGENTS.md`](https://github.com/Knuckles-Team/graph-os/blob/main/AGENTS.md#w5-source-measurements-measured-2026-09-12)
"W5 source measurements".
