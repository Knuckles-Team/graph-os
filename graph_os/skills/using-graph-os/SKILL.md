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

Graph OS is an MCP server (and REST twin) that fronts a fleet of other MCP
servers behind a small set of **always-on** tools plus a **discover, load,
call** lifecycle for everything else. This skill describes what is actually
callable today; one section at the end names what is staged but not yet
reachable.

## Discover before concluding a tool doesn't exist

Most of the fleet (hundreds of tools across dozens of servers) is not in your
tool list until you load it. Four meta-tools, always present, make the fleet
discoverable and loadable (`graph_os/fleet/multiplexer.py`):

- `find_tools(query, top_k=0)` — semantic search of the **entire** fleet (and
  any fleet-served `skill://` resources) for a natural-language task. Call
  this first before assuming a capability is missing. Returns ranked,
  prefixed names plus an `unavailable` map of unreachable servers.
- `list_catalog(server="", include_tools=true)` — flat browse of every
  configured server; pass `server` to drill into one server's full tool list.
- `load_tools(tools=[...], servers=[...], auto_unload=false)` — mounts tools
  or whole servers so they become callable server-side this session. This
  also works for graph-os's own granular tools when a condensed profile has
  held them back (`MCP_TOOL_MODE=intent`): pass the bare name, e.g.
  `load_tools(tools=["graph_query"])`. `auto_unload=true` retracts a tool
  after its next call (one-shot use).
- `unload_tools(tools=[...], servers=[...], toolsets=[...])` — retract
  loaded tools; `servers=["graph-os"]` retracts graph-os's own condensed
  surface at once. Nothing is deleted; `load_tools` brings it straight back.
- `multiplexer_status()` — health of every aggregated child server (state,
  restart count, concurrency, in-flight/queued calls), no arguments.

A skill is just another catalog item: `find_tools("...")` ranks
`skill://<name>/SKILL.md` resources alongside tools. `load_tools(tools=
["skill://<name>/SKILL.md"])` returns that skill's body (its `SKILL.md` text)
in the load result — read and follow it directly, or hand it to
`graph_orchestrate` with `skill_name=<name>` to have a delegated agent run it
instead of running it yourself.

The MCP server's own `initialize` instructions (`graph_os/mcp_server/
runtime.py`, `_build_server`) restate this same discover-first rule to every
connecting client, so the above is also what any agent is told directly on
connect.

## Query the knowledge graph before reading code

Before grepping source, use graph-os's own always-on tools — they are
registered under these exact names (`agent_utilities/mcp/tools/`, served
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
  runtime configuration, including the always-load set
  (`key="MCP_ALWAYS_LOAD"`).

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
