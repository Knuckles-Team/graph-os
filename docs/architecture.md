# How Graph OS fits

GraphOS is the platform edge and composition root. It is the running process
that receives requests, establishes authority, and projects independently owned
capabilities through one governed surface.

![Knuckles platform runtime architecture](assets/runtime-architecture.svg)

## One request, five clear owners

| Layer | Owns | GraphOS relationship |
|---|---|---|
| [Agent Web UI](https://knuckles-team.github.io/agent-webui/) | Browser interaction, chat presentation, and local UI state | Hosted by GraphOS with an injected application composer. |
| Agent Terminal UI | Terminal and headless operation | Uses GraphOS REST today; conversational ACP chat remains an explicit contract gap. |
| Geniusbot | Desktop cockpit for chat, graph, fleet, and health | Uses the governed GraphOS gateway for its primary panels. |
| Messaging channels | Chat and voice entrypoints | Hosted and supervised by GraphOS; adapters and the inbound router remain owned by agent-utilities. |
| **GraphOS** | MCP, REST, A2A, request policy, fleet supervision, and process lifecycle | Authenticates, composes, routes, supervises, and projects. |
| [agent-utilities](https://knuckles-team.github.io/agent-utilities/) | Agent decisions, workflows, evaluations, and skills | Provides typed application services to GraphOS. |
| [epistemic-graph](https://knuckles-team.github.io/epistemic-graph/) | Durable multimodal state, RDF/OWL/SHACL reasoning, queries, and provenance | Provides generated storage and reasoning contracts. |
| [agent-connector-sdk](https://knuckles-team.github.io/agent-connector-sdk/) | Connector servers, source synchronization, and governed source effects | Defines the services admitted into the GraphOS fleet. |

## Runtime flow

<ol class="site-flow" aria-label="GraphOS request flow">
  <li class="site-flow__step"><span class="site-flow__title">Receive</span><span class="site-flow__body">Agent Web UI, Agent Terminal UI, Geniusbot, messaging, MCP, REST, and A2A converge on one GraphOS process.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Authorize</span><span class="site-flow__body">GraphOS verifies identity, tenant, scope, and action policy.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Delegate</span><span class="site-flow__body">Agent work reaches agent-utilities; source work reaches an admitted connector.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Commit</span><span class="site-flow__body">Governed reads and mutations reach epistemic-graph with evidence and provenance.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Project</span><span class="site-flow__body">GraphOS returns the same result through the caller's selected transport.</span></li>
</ol>

## Composition invariants

- MCP and REST handlers meet at the same application boundary.
- The serving lifecycle owns one FastMCP loop and one fleet multiplexer.
- Durable state and semantic reasoning are performed by epistemic-graph.
- Agent policy and workflow decisions are performed by agent-utilities.
- Connector credentials and vendor effects stay inside connector services.
- Browser presentation and local interaction state stay inside Agent WebUI.
- Missing authority produces an explicit unavailable result; GraphOS does not
  manufacture state or success receipts.

Use the [capability status](status.md) page for the exact surface exposed by the
current package.
