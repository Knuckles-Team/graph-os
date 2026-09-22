# GraphOS

[![GitHub Repo stars](https://img.shields.io/github/stars/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/forks)
[![GitHub contributors](https://img.shields.io/github/contributors/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/graphs/contributors)
[![GitHub license](https://img.shields.io/github/license/Knuckles-Team/graph-os)](LICENSE)
[![GitHub last commit (by committer)](https://img.shields.io/github/last-commit/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/commits/main)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/pulls)
[![GitHub closed pull requests](https://img.shields.io/github/issues-pr-closed/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/pulls?q=is%3Apr+is%3Aclosed)
[![GitHub issues](https://img.shields.io/github/issues/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os/issues)
[![GitHub top language](https://img.shields.io/github/languages/top/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![GitHub language count](https://img.shields.io/github/languages/count/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![GitHub repo size](https://img.shields.io/github/repo-size/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![GitHub repo file count (file type)](https://img.shields.io/github/directory-file-count/Knuckles-Team/graph-os)](https://github.com/Knuckles-Team/graph-os)
[![PyPI - Version](https://img.shields.io/pypi/v/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - Downloads](https://img.shields.io/pypi/dd/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - License](https://img.shields.io/pypi/l/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - Wheel](https://img.shields.io/pypi/wheel/graph-os)](https://pypi.org/project/graph-os/)
[![PyPI - Implementation](https://img.shields.io/pypi/implementation/graph-os)](https://pypi.org/project/graph-os/)
[![MCP Server](https://badge.mcpx.dev?type=server "MCP Server")](https://github.com/Knuckles-Team/graph-os)
[![Build](https://github.com/Knuckles-Team/graph-os/actions/workflows/release.yml/badge.svg)](https://github.com/Knuckles-Team/graph-os/actions/workflows/release.yml)
[![Documentation](https://github.com/Knuckles-Team/graph-os/actions/workflows/pages.yml/badge.svg)](https://knuckles-team.github.io/graph-os/)

## Overview

GraphOS is the service composition runtime for the Knuckles agent platform. It hosts public MCP, REST, and A2A surfaces, supervises the tool fleet, applies control-plane policy, and can serve Agent WebUI.

*Version: 0.1.0*

## Key Capabilities

- Run local MCP over stdio or authenticated MCP over HTTP.
- Share application services across MCP and REST routes.
- Discover and supervise the authorized MCP fleet.
- Apply identity, tenant, and action policies.
- Host WebUI and provide health, configuration, and deployment tools.

## Documentation

The [GraphOS documentation](https://knuckles-team.github.io/graph-os/) includes the [MCP server](https://knuckles-team.github.io/graph-os/mcp-server/), [gateway](https://knuckles-team.github.io/graph-os/gateway/), [capability status](https://knuckles-team.github.io/graph-os/status/), and [deployment guide](https://knuckles-team.github.io/graph-os/deployment/).

## Architecture

GraphOS composes authenticated clients with application services and the MCP fleet. Agent Utilities owns agent workflows, epistemic-graph owns durable graph state and reasoning, and the connector SDK owns connector transport and source synchronization.

## Quick Start

Requires Python 3.12–3.14 and uv. From a checkout, install the project, create a local profile, and launch the stdio MCP server:

```bash
git clone https://github.com/Knuckles-Team/graph-os.git
cd graph-os
uv sync
uv run setup-config generate --profile tiny
uv run graph-os --transport stdio
```

For identity settings or network deployment, follow the [deployment guide](https://knuckles-team.github.io/graph-os/deployment/).

## Contributing

Issues and pull requests are welcome. Read [AGENTS.md](AGENTS.md) for contribution boundaries and checks. Report security issues through [GitHub Security Advisories](https://github.com/Knuckles-Team/graph-os/security/advisories/new).

## License

GraphOS is released under the [MIT License](LICENSE).
