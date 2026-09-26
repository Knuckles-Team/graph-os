# A2A facade

GraphOS exposes one authenticated A2A boundary over agent-utilities' public
agent control plane, whose WorkItems are durable epistemic-graph records. It
does not create an A2A-specific task database, broker, worker, event log, or
orchestration loop.

## Discovery and transports

The authenticated Agent Card is served at
`/.well-known/agent-card.json`. The card advertises JSON-RPC at `/a2a`,
`text/plain` messages, bearer
security and streaming, and truthfully disables push notifications and state
transition history.

The same service is projected as the `graph_a2a` MCP tool and its exact
action-routed REST twin, `POST /graph/a2a`. Supported operations are:

| A2A JSON-RPC | `graph_a2a` action | Required scope |
|---|---|---|
| `message/send` | `send` | `kg:write` |
| `message/stream` (SSE, native route only) | — | `kg:write` |
| `tasks/resubscribe` (SSE, native route only) | — | `kg:read` |
| `tasks/get` | `get` | `kg:read` |
| `tasks/list` | `list` | `kg:read` |
| `tasks/cancel` | `cancel` | `kg:write` |
| Agent Card | `card` | `kg:read` |

`message/send` requires an `Idempotency-Key` header on the native A2A route and
an `idempotency_key` argument on MCP/REST. Reusing a key with the same normalized
request returns the same task. Reusing it with a different request is a conflict.
Task reads, listing cursors, and cancellation are isolated to the verified
tenant and task owner.

## Streaming

`message/stream` admits one task exactly like `message/send`, then answers
`text/event-stream`. Each SSE event carries one JSON-RPC frame: the task first,
then one `status-update` per state transition, the last with `final: true`.
The stream follows the durable WorkItem through the control plane, backs off
to a bounded poll interval, and ends after 15 minutes without a final state;
the client then resubscribes. Every event has a resumable `id`
(task, state, timestamp). `tasks/resubscribe` re-attaches to an owned task and
skips the state named by `Last-Event-ID`. A failure ends the stream with a
JSON-RPC error frame. There is no event-history replay beyond the current
state.

<ol class="site-flow" aria-label="A2A request flow">
  <li class="site-flow__step"><span class="site-flow__title">Authenticate</span><span class="site-flow__body">A2A, MCP, or REST establishes a verified bearer GraphSession.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Route</span><span class="site-flow__body">EG AgentAssemble proves and records the agent graph; the control plane resolves an agent when it abstains.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Admit</span><span class="site-flow__body">The control plane admits one EG WorkItem under tenant, ownership, and idempotency fences.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Dispatch</span><span class="site-flow__body">agent-utilities signs and enqueues the agent turn.</span></li>
  <li class="site-flow__step"><span class="site-flow__title">Record</span><span class="site-flow__body">RunTrace and tool-call provenance bind the result to the request.</span></li>
</ol>

## Routing and tool-subset assembly

Routing never searches free text. A message declares a typed task in metadata
`graphOsTaskIris` (`eg:task/research`, `implement`, `review`, `operate` or
`communicate`) or names an agent in `graphOsAgentName`; the control plane then
resolves an authorized agent. A message with neither is refused.

When Graph OS runs with Decide installed, a typed message goes to
epistemic-graph `AgentAssemble` first. A typed message declares task IRIs in
`graphOsTaskIris`, capability IRIs in `graphOsCapabilityIris`, or both. The
text of the message is never a requirement; only its digest is sent.

A solved assembly is handled in this order:

1. The decision record is committed with `DecisionCommit`.
2. The assembled agents are published.
3. The routed agent graph is published with the committed record as its
   `synthesis_evidence`.
4. The route is used. It carries the graph and the record.

If there is no budget and the assembly abstains, the engine is unavailable,
or the record cannot be committed, the control plane's capability search
answers instead. A decision that cannot be recorded is never acted on.

A request with `contextBudgetTokens` is routed only by assembly. Any outcome
other than a solved assembly fails closed with the reason, because a budget
is never silently ignored. The proved tool subset travels with the dispatch
as a signed allowed-tool list that the agent worker enforces.

`find_tools` with `context_budget_tokens` and `capability_iris` asks the same
assembly for the smallest covering tool subset. This is evaluate-only: one
record in 16 is committed as an audit sample. Otherwise the ranked tools
answer, with the reason. Push notifications are not advertised.

## Answers

When a streamed task completes, its answer (the agent run's bounded, redacted
final output from the control plane) is sent as one `artifact-update` event
before the final status event. `tasks/resubscribe` to a completed task sends
it too.
