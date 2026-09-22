# GraphOS

[![PyPI - Version](https://img.shields.io/pypi/v/graph-os)](https://pypi.org/project/graph-os/)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-0ea5e9)](https://knuckles-team.github.io/graph-os/)
[![GitHub license](https://img.shields.io/github/license/Knuckles-Team/graph-os)](LICENSE)

**The process you run to open the Knuckles agent platform to MCP, REST, A2A,
and the browser.**

GraphOS is the governed runtime door: it authenticates requests, exposes one
application surface, supervises the MCP tool fleet, and hosts Agent WebUI. The
agents, database, and connector implementations remain independently owned and
meet here through public contracts.

![Knuckles platform runtime architecture](docs/assets/runtime-architecture.svg)

<details>
<summary>Project telemetry</summary>

[![Build](https://github.com/Knuckles-Team/graph-os/actions/workflows/release.yml/badge.svg)](https://github.com/Knuckles-Team/graph-os/actions/workflows/release.yml)
[![Pages](https://github.com/Knuckles-Team/graph-os/actions/workflows/pages.yml/badge.svg)](https://github.com/Knuckles-Team/graph-os/actions/workflows/pages.yml)
[![MCP Server](https://badge.mcpx.dev?type=server "MCP Server")](https://github.com/Knuckles-Team/graph-os)
[![PyPI - Downloads](https://img.shields.io/pypi/dd/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - License](https://img.shields.io/pypi/l/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - Wheel](https://img.shields.io/pypi/wheel/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - Implementation](https://img.shields.io/pypi/implementation/graph-os)](https://pypi.org/project/graph-os/)
[![GitHub Repo stars](https://img.shields.io/github/stars/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/forks)
[![GitHub contributors](https://img.shields.io/github/contributors/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/graphs/contributors)
[![GitHub last commit (by committer)](https://img.shields.io/github/last-commit/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/commits/main)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/pulls)
[![GitHub closed pull requests](https://img.shields.io/github/issues-pr-closed/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/pulls?q=is%3Apr+is%3Aclosed)
[![GitHub issues](https://img.shields.io/github/issues/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/issues)
[![GitHub top language](https://img.shields.io/github/languages/top/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![GitHub language count](https://img.shields.io/github/languages/count/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![GitHub repo size](https://img.shields.io/github/repo-size/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![GitHub repo file count (file type)](https://img.shields.io/github/directory-file-count/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)

</details>

## Overview

Run GraphOS when an MCP client, HTTP application, A2A peer, or Agent WebUI needs
one authenticated way into the platform. One serving lifecycle composes the
same application services across transports, applies identity and tenant
policy, and keeps tool discovery tied to the live fleet catalog.

GraphOS is the platform edge and composition root. It is not the agent harness,
the durable database, a connector implementation, or the browser UI.

## Key Capabilities

- Serve local MCP over `stdio` and authenticated MCP over streamable HTTP.
- Project the same governed operations through REST and unary A2A.
- Discover, admit, supervise, and route the MCP connector fleet.
- Apply identity, tenant, action-policy, idempotency, and provenance controls.
- Host Agent WebUI and coordinate attended browser capabilities.
- Generate deployment configuration and report live readiness through `/health`.

## Documentation

Start with the **[visual platform guide](https://knuckles-team.github.io/graph-os/)**,
then use the task-oriented references:

- [Start GraphOS](https://knuckles-team.github.io/graph-os/get-started/)
- [Understand the architecture](https://knuckles-team.github.io/graph-os/architecture/)
- [Check the exact capability surface](https://knuckles-team.github.io/graph-os/status/)
- [Configure a deployment](https://knuckles-team.github.io/graph-os/deployment/)

## Architecture

| Layer | Authority | Relationship to GraphOS |
|---|---|---|
| [Agent WebUI](https://knuckles-team.github.io/agent-webui/) | Browser experience and local interaction state | GraphOS hosts it and supplies governed application routes. |
| **GraphOS** | MCP, REST, A2A, identity policy, fleet supervision | The running process and public door. |
| [agent-utilities](https://knuckles-team.github.io/agent-utilities/) | Agents, workflows, evaluation, and skills | GraphOS invokes its typed control-plane services. |
| [epistemic-graph](https://knuckles-team.github.io/epistemic-graph/) | Durable graph, SQL, RDF, vector, time, reasoning, and provenance | GraphOS uses its generated client contracts. |
| [agent-connector-sdk](https://knuckles-team.github.io/agent-connector-sdk/) | Connector servers, source synchronization, and governed effects | GraphOS discovers and routes SDK-based connector services. |

The boundary is deliberate: GraphOS authenticates, composes, routes,
supervises, and projects. Each sibling remains the source of truth for its own
domain.

## Quick Start

Requires Python 3.12–3.14 and [`uv`](https://docs.astral.sh/uv/). Install the
bundled runtime, generate the local profile, and register its `stdio` launcher
with Codex:

```bash
uv tool install "graph-os[webui]"
setup-config generate --profile tiny
setup-config doctor --profile tiny
setup-config codex
```

GraphOS now appears as the `graph-os` MCP server in Codex. Other MCP clients can
launch the same local transport directly:

```bash
graph-os --transport stdio
```

Network deployments must use the identity, TLS, and authorization settings in
the [deployment guide](https://knuckles-team.github.io/graph-os/deployment/).

## Contributing

Issues and pull requests are welcome. Read [AGENTS.md](AGENTS.md) for source
boundaries and quality gates. Report vulnerabilities through
[GitHub Security Advisories](https://github.com/Knuckles-Team/graph-os/security/advisories/new).

### Development

Use an isolated Git worktree, install the `test` extra, and run the focused
tests plus repository hooks described in [AGENTS.md](AGENTS.md).

## License

GraphOS is released under the [MIT License](LICENSE).
