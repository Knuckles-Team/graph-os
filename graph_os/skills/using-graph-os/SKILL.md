---
name: using-graph-os
skill_type: skill
description: >-
  Use the Graph OS MCP server as a connecting agent: discover whether a tool
  or skill exists before assuming it is missing, query the knowledge graph
  before reading source, and delegate work to an ingested skill/workflow/
  agent. Use this before grepping the codebase, before concluding a
  capability is unavailable, or before hand-rolling a task graph-os can run
  for you.
---

# Using Graph OS

Graph OS is an MCP server (and REST twin) that serves **one tool contract**:
the intent tools `ask` (read), `find` (discover), `write`, `act` (run),
`manage` (configure, approve, load fleet servers) and `why` (explain), plus
two MCP Apps launchers. Every graph-os operation and every fleet tool is
reached through these tools. This skill describes what is callable today.

## Call shape

Each intent tool takes `action="<tool>.<op>"` plus `params={...}`, or a
natural-language `intent` with `action` empty.

- `find(action="describe")` lists every operation.
  `find(action="describe", params={"action": "<op>"})` returns one
  operation's arguments.
- `write`, `act` and `manage` preview first. Resubmit the returned
  `plan_ref` with `execute=true` to run the plan.

## Discover before concluding a tool doesn't exist

The MCP fleet (hundreds of tools across dozens of servers) is reached
through the same tools (`graph_os/fleet/multiplexer.py`):

- `find(action="tools", intent="...")` — semantic search of the **entire**
  fleet, including fleet-served `skill://` resources. Call it before
  assuming a capability is missing. It returns ranked, prefixed names and
  an `unavailable` map of unreachable servers.
- `find(action="catalog")` — browse every configured server.
  `params={"server": "<name>"}` drills into one server's tools.
- `find(action="status")` — health of every fleet child server, the
  unadmitted servers and the last onboarding pass.
- `act(action="fleet.call", params={"tool": "<prefixed name>",
  "arguments": {...}})` — calls one fleet tool.
- `manage(action="fleet.load"|"fleet.unload", params={...})` — mounts or
  releases fleet servers ahead of use. A call mounts what it needs, so
  this step is optional.

A skill is a catalog item: `find(action="tools", ...)` ranks
`skill://<name>/SKILL.md` resources beside tools. Read the skill body and
follow it, or hand it to `graph_orchestrate` with `skill_name=<name>`.

Browser control (`browser_control`), A2A tasks (`graph_a2a`) and the RLM
control plane (`graph_rlm`) are `act` operations, not listed tools.

The MCP server's `initialize` instructions (`graph_os/mcp_server/
runtime.py`, `_build_server`) restate this contract to every client.

## Query the knowledge graph before reading code

Before grepping source, use graph-os's own operations. Reach each one
through an intent tool, e.g. `ask(action="graph_code.code_context",
params={...})`. They are registered under these exact names (`agent_utilities/mcp/tools/`, served
through graph-os's `REGISTERED_TOOLS`/`_execute_tool` dispatch in
`graph_os/mcp_server/runtime.py`, with a REST `/api` twin):

- `graph_code(action="code_context", target="<symbol or area>", direction="how"|"usage"|"impact")`
  — a cited answer about how code works, how it's used, or what depends on
  it. Other `action` values: `cross_repo_usages`, `call_graph`,
  `similar_code`, `routes`, `change_coupling`, `code_evolution`,
  `blast_radius`, `code_metrics`, `arch_report`, `adr`.
- `graph_query(query, ...)` — a read-only Cypher, bounded UQL, SQL, SPARQL,
  or federated graph query, returning one typed evidence bundle.
- `graph_search(query, mode="hybrid", top_k=10, ...)` — search the graph by
  strategy: `hybrid` (default), `concept` (look up a `CONCEPT:ID`),
  `analogy`, `memory`, `discover`, and others described in the tool's own
  `mode` parameter.
- `graph_config(action="get"|"describe"|"set", key=...)` — inspect or change
  runtime configuration.

Prefer these over reading files directly when the question is "how does this
work," "who calls this," or "what would this change affect" — the graph is
kept current by ingestion, where a stale local checkout is not.

## Delegating work to an agent or workflow

`graph_orchestrate(task, agent_name="", skill_name="", tool_server="",
execution_mode="auto", allowed_tools=..., ...)` resolves an ingested
skill/workflow/agent for `task` and runs it on the governed delegation
runtime (registered under that exact name in agent-utilities; dispatched from
graph-os through `graph_os/mcp_server/runtime.py`'s `_execute_tool`). Leave
`agent_name`/`skill_name` empty to let the
knowledge graph's own capability ranking pick the best skill, workflow, or
fleet tool for the task; pass `agent_name` (or an exact `skill_name` +
`tool_server`) to pin a specific one. The result carries the resolution,
run/session handles, tool-call provenance, and any approval request raised
along the way — read it rather than assuming success.

There is no separate `action="execute_agent"` / `action="execute_workflow"`
parameter on this tool today; resolution is driven by which of `task`,
`agent_name`, and `skill_name` you pass.

## Changing soon

A six-verb intent surface (`find`, `ask`, `why`, `write`, `act`, `manage`)
already exists in this repository's source
(`graph_os/api/mcp/verbs.py`, with a REST twin in `graph_os/api/http/
app.py` whose own docstring calls it "mounted by the later cutover lane") but
nothing in `graph_os/mcp_server/` imports or serves it yet — it is not
reachable by any client today. Expect the always-on tools above to collapse
behind those six verbs once that cutover lands; until then, use the tool
names in this skill, not the six verbs.
