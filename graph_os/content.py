"""GraphOS-owned declarative content provider."""

from __future__ import annotations

from pathlib import Path

from agent_connector_sdk.mcp.content import ConnectorContent

from graph_os._version import __version__


def connector_content() -> ConnectorContent:
    """Return GraphOS's independently identified SDK content provider."""

    return ConnectorContent(
        connector="graph-os",
        package_root=Path(__file__).resolve().parent,
        package_version=__version__,
    )


__all__ = ["connector_content"]
