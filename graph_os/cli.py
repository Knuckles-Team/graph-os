"""graph-os console entry point.

This is a **real, minimal** entry point, not a stub that pretends to serve:
it reports the installed `graph_os` version and the target composition this
repository will host once RF-ADR-009 Migration Wave 5 extracts the MCP
server, REST gateway, control plane, fleet gateway, webui hosting, and
deployment tooling out of `agent-utilities`. Until that extraction lands,
`graph-os` (the running service) continues to be `agent_utilities.mcp.kg_server`
— see `services/graph-os/AGENTS.md` for the live deployment and
`AGENTS.md` in this repository for scope and status.
"""

from __future__ import annotations

import argparse
import sys

from graph_os._version import __version__

_DESCRIPTION = (
    "graph-os: the deployable composition for the agent-utilities agent "
    "plane (MCP server, REST gateway, control plane, fleet gateway, "
    "agent-webui hosting, deployment tooling) per RF-ADR-009. This "
    "repository is a pre-extraction scaffold — see AGENTS.md for the "
    "Migration Wave 5 plan and current status."
)


def build_parser() -> argparse.ArgumentParser:
    """Build the `graph-os` argument parser."""
    parser = argparse.ArgumentParser(prog="graph-os", description=_DESCRIPTION)
    parser.add_argument(
        "--version",
        action="version",
        version=f"graph-os {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `graph-os` console script.

    With no arguments, prints help (there is no server to start yet) and
    exits `0`. `--version`/`--help` are handled by `argparse` itself.
    """
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
