# GRAPHOS-GWC — Gateway carry-over from agent-utilities

Status: PROPOSED. Owner: graph-os. Cross-repo IDs: AU-BOUNDARY-R001.2, AU-BOUNDARY-R001.3, AU-BOUNDARY-R001.7 (agent-utilities deletions); GRAPHOS-HOST-R005 (gateway reconciliation parent).
Public provenance: agent-utilities deletion commits 0eb67c99c (dashboard router), 902f906ca (Live Artifacts router), 7717d2e62 (genius_agent widget).

## Purpose and user stories

agent-utilities deleted three gateway modules on the grounds that graph-os is the sole HTTP door. An operator or the web UI that used the old dashboard routes, the Live Artifacts HTTP routes, or the Genius Agent dashboard widget must keep working against graph-os. This spec records, route by route, what graph-os already serves, what must still be built, and what is intentionally dropped.

## Requirements and acceptance

- FR-001: Every dashboard route of the deleted router is served by graph-os with the same read/write capability split and the same refusal behaviour. Finding: all 15 routes plus the dashboard websocket already exist in the graph-os dashboard router; the carry-over only adds the missing proof.
- FR-002: The Live Artifacts create, get and refresh routes are served by graph-os over the agent-utilities Live Artifact store through a public `agent_utilities.api` export, with the same bounded-input refusal and preserved-render-on-failure behaviour. Finding: no equivalent exists in graph-os; this is to build.
- FR-003: The Genius Agent widget is not carried over. Its data was constant (agents=1, skills=120, tools=200, status "Active") and never read a service, so serving it would fabricate a healthy status. Fleet and tool counts are served by the fleet and catalog surfaces instead.
- SC-001: A route census test lists every old route in the parity table and fails when a row has no served route or no recorded drop reason.

## Scope and interfaces

See [plan.md](plan.md) for the functional description, wiring and parity table. Non-goals: moving the Live Artifact store out of agent-utilities, changing the dashboard layout format, new widgets.

## Traceability

| Requirement | Design section | Test ID | Evidence |
|---|---|---|---|
| FR-001 | plan.md Parity table | T-001, T-002 | PENDING |
| FR-002 | plan.md Design in this repository | T-003 to T-007 | PENDING |
| FR-003 | plan.md Parity table | T-008 | PENDING |

## Open questions

- Which agent-utilities module exports the Live Artifact store and refresh service in `agent_utilities.api`? Today they live only in the agent-utilities knowledge-graph package and graph-os may not import that package directly (GRAPHOS-HOST-R002). The prerequisite row below tracks it.
