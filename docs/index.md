<section class="site-hero" aria-labelledby="graphos-title">
  <p class="site-hero__eyebrow">The governed runtime door</p>
  <h1 class="site-hero__title" id="graphos-title">Run the whole agent platform through one clear boundary.</h1>
  <p class="site-hero__summary">
    GraphOS is the process you run. It authenticates MCP, REST, A2A, and browser
    requests; composes the agent and graph services behind them; and supervises
    the connector fleet from one serving lifecycle.
  </p>
  <div class="site-hero__actions">
    <a class="md-button md-button--primary" href="get-started/">Start GraphOS</a>
    <a class="md-button" href="architecture/">See how it fits</a>
  </div>
</section>

<figure class="site-architecture" markdown>
  ![Knuckles platform runtime architecture](assets/runtime-architecture.svg)
  <figcaption>One request path, five deliberately separate authorities.</figcaption>
</figure>

<div class="site-card-grid" markdown>
  <article class="site-card" markdown>
    <p class="site-card__title">Try it</p>
    <p class="site-card__body">
      Install the bundled runtime, generate a local profile, validate it, and
      register the MCP launcher with Codex.
    </p>
    <a href="get-started/">Open the quick start →</a>
  </article>
  <article class="site-card" markdown>
    <p class="site-card__title">Understand it</p>
    <p class="site-card__body">
      Follow a request from client to GraphOS, agent control plane, durable
      graph, and connector service without blurring ownership.
    </p>
    <a href="architecture/">Explore the architecture →</a>
  </article>
  <article class="site-card" markdown>
    <p class="site-card__title">Operate it</p>
    <p class="site-card__body">
      Configure local or network transports, validate authority, inspect
      readiness, and run the release canary.
    </p>
    <a href="deployment/">Use the deployment guide →</a>
  </article>
</div>

## What GraphOS owns

<div class="site-ownership">
  <div class="site-ownership__grid">
    <div class="site-ownership__item">
      <span class="site-ownership__label">GraphOS owns</span>
      <span class="site-ownership__value">Process lifecycle, MCP and REST composition, unary A2A, request policy, fleet supervision, and optional WebUI hosting.</span>
    </div>
    <div class="site-ownership__item">
      <span class="site-ownership__label">GraphOS delegates</span>
      <span class="site-ownership__value">Agent decisions to agent-utilities, durable state and reasoning to epistemic-graph, source effects to connector services, and presentation to Agent WebUI.</span>
    </div>
  </div>
</div>

GraphOS does not copy those authorities. It binds their public contracts into
one identity-aware application surface and reports unavailable authority
explicitly.

## Follow one request

<ol class="site-flow">
  <li class="site-flow__step">
    <span class="site-flow__title">Enter through one door</span>
    <span class="site-flow__body">An MCP, REST, A2A, or WebUI request reaches the GraphOS serving process.</span>
  </li>
  <li class="site-flow__step">
    <span class="site-flow__title">Establish authority</span>
    <span class="site-flow__body">GraphOS verifies identity, tenant, requested action, and the applicable policy.</span>
  </li>
  <li class="site-flow__step">
    <span class="site-flow__title">Route to the owner</span>
    <span class="site-flow__body">Agent work reaches agent-utilities; graph work reaches epistemic-graph; source work reaches an admitted connector.</span>
  </li>
  <li class="site-flow__step">
    <span class="site-flow__title">Return evidence</span>
    <span class="site-flow__body">The caller receives a transport-specific response backed by the same governed result and provenance.</span>
  </li>
</ol>

## Choose the right room

| If you need to… | Go to… |
|---|---|
| Run MCP, REST, A2A, WebUI, or fleet supervision | **GraphOS** — you are here |
| Build agents, workflows, evaluations, or skills | [agent-utilities](https://knuckles-team.github.io/agent-utilities/) |
| Store, query, reason over, or prove durable knowledge | [epistemic-graph](https://knuckles-team.github.io/epistemic-graph/) |
| Build and certify a source connector | [agent-connector-sdk](https://knuckles-team.github.io/agent-connector-sdk/) |
| Use the platform in a browser | [Agent WebUI](https://knuckles-team.github.io/agent-webui/) |

The [capability status](status.md) is the exact account of the surfaces shipped
by this package.
