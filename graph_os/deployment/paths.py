"""Paths for the GraphOS host's shared XDG configuration and data.

The existing agent-utilities directory names are a persistent deployment
contract. GraphOS owns these host path resolvers while agent-utilities keeps
its own agent and model paths.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
from agent_utilities.core.config import setting

_APP_NAME = "agent-utilities"
_APP_AUTHOR = "knuckles-team"


def config_dir() -> Path:
    """Resolve the shared operator configuration root."""
    override = setting("AGENT_UTILITIES_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_path(_APP_NAME, _APP_AUTHOR))


def data_dir() -> Path:
    """Resolve the shared runtime data root."""
    override = setting("AGENT_UTILITIES_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_data_path(_APP_NAME, _APP_AUTHOR))


def mcp_config_path() -> Path:
    """Resolve the cross-IDE MCP catalog read by the GraphOS host."""
    return config_dir() / "mcp_config.json"
