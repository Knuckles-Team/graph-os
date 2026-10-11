# GRAPHOS-GWC-001 requirements

| ID | Requirement | Verification |
|---|---|---|
| `GRAPHOS-GWC-R001` | **Dashboard route parity (replaces AU-BOUNDARY-R001.2, commit 0eb67c99c).** Every route of the deleted dashboard router is served by graph-os with the same capability split and refusals. Already provided; this row adds proof only. | tests/gateway/test_dashboard_route_census.py passes. |
| `GRAPHOS-GWC-R001.1` | Add one test in tests/gateway/test_dashboard_route_census.py asserting the served dashboard router exposes all 15 old method and path pairs. | The test fails when a route is removed. |
| `GRAPHOS-GWC-R001.2` | Add one test in tests/gateway/test_dashboard_api.py asserting read-capability callers get 403 on PUT /layout, POST /daemon/start, POST /hydrate and POST /hydrate/{source}, while API-key callers pass. | The test passes. |
| `GRAPHOS-GWC-R001.3` | Add one test in tests/gateway/test_dashboard_api.py asserting GET /health returns 200 without a capability and sets Cache-Control no-store. | The test passes. |
| `GRAPHOS-GWC-R001.4` | Add one test in tests/gateway/test_dashboard_api.py asserting a malformed hydrate source returns 422 and an unknown service id returns 404. | The test passes. |
| `GRAPHOS-GWC-R002` | **Live Artifacts routes (replaces AU-BOUNDARY-R001.3, commit 902f906ca).** graph-os serves create, get and refresh for Live Artifacts over the agent-utilities store. To build. | The artifact tests below pass and the routes are mounted by the host. |
| `GRAPHOS-GWC-R002.1` | **Cross-repo prerequisite (owner: agent-utilities).** Export the Live Artifact store getter, artifact model, refresh service and bounded-JSON error through a public agent_utilities.api module. | An agent-utilities test imports each name from the public module. |
| `GRAPHOS-GWC-R002.2` | Add one typed model: create-artifact request in graph_os/gateway/artifacts_api.py, rejecting empty template and oversize data. | Refusal tests in tests/gateway/test_artifacts_api.py. |
| `GRAPHOS-GWC-R002.3` | Add one port: artifact store and refresh ports plus a resolver callable type in graph_os/gateway/ports.py. | tests/gateway/test_ports.py checks the ports are protocols. |
| `GRAPHOS-GWC-R002.4` | Add one route: POST /api/artifacts, returning id and rendered output, 400 on a bounded-JSON error. | test_artifacts_api.py create and refusal cases. |
| `GRAPHOS-GWC-R002.5` | Add one route: GET /api/artifacts/{artifact_id}, 404 when absent. | test_artifacts_api.py get and 404 cases. |
| `GRAPHOS-GWC-R002.6` | Add one route: POST /api/artifacts/{artifact_id}/refresh with inline data or resolver, 404 when absent, prior render preserved on failure. | test_artifacts_api.py refresh, 404 and failure cases. |
| `GRAPHOS-GWC-R002.7` | Wire one call site: a register function in graph_os/gateway/artifacts_api.py called where the host mounts the graph and usage routes, with gateway:read on GET and gateway:write on POST. | tests/gateway/test_artifacts_route_mount.py asserts the routes and the 403 cases. |
| `GRAPHOS-GWC-R002.8` | Add one function: a graph-backed source resolver in graph_os/gateway/artifacts_api.py that re-derives from source node ids and otherwise preserves prior data. | test_artifacts_api.py resolver cases. |
| `GRAPHOS-GWC-R003` | **Genius Agent widget dropped (replaces AU-BOUNDARY-R001.7, commit 7717d2e62).** The widget is intentionally not carried over because it returned constant data. | The gate test passes. |
| `GRAPHOS-GWC-R003.1` | Add one test in tests/gateway/test_registry.py asserting genius_agent is absent from the widget registry and no fixed-value widget is registered. | The test passes. |
