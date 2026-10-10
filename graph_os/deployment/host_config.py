"""GraphOS-owned hosting/deployment configuration settings (GRAPHOS-HOST-R010).

A typed, validated model for the hosting and deployment settings that
GraphOS owns in its own right -- split out of the agent runtime's
monolithic ``agent_utilities.core.config`` module. Connector base settings
belong to the connector SDK's own configuration module; agent/model
settings remain with the agent runtime. This module resolves without
importing ``agent_utilities.core.config`` internals.

Existing call sites across ``graph_os/gateway`` and ``graph_os/messaging``
still read settings through ``agent_utilities.core.config`` directly; this
is the typed model for the first slice of the migration (GRAPHOS-HOST-R010
``.1``). Routing each call site onto this module is follow-up work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class InvalidHostConfigError(ValueError):
    """A hosting/deployment setting failed validation."""


@dataclass(frozen=True)
class HostingDeploymentSettings:
    """GraphOS's own view of its hosting and deployment settings."""

    bind_host: str
    bind_port: int
    public_base_url: str
    deployment_environment: str

    def __post_init__(self) -> None:
        if not self.bind_host.strip():
            raise InvalidHostConfigError("bind_host must not be empty")
        if not (0 < self.bind_port < 65536):
            raise InvalidHostConfigError(
                f"bind_port must be between 1 and 65535, got {self.bind_port}"
            )
        if not self.public_base_url.strip():
            raise InvalidHostConfigError("public_base_url must not be empty")
        if self.deployment_environment not in {"development", "staging", "production"}:
            raise InvalidHostConfigError(
                "deployment_environment must be one of "
                "'development', 'staging', 'production', got "
                f"{self.deployment_environment!r}"
            )


def load_hosting_deployment_settings(
    env: dict[str, str] | None = None,
) -> HostingDeploymentSettings:
    """Resolve hosting/deployment settings from GraphOS's own environment
    variables only -- never from ``agent_utilities.core.config`` internals."""
    source = env if env is not None else os.environ
    return HostingDeploymentSettings(
        bind_host=source.get("GRAPH_OS_BIND_HOST", "127.0.0.1"),
        bind_port=int(source.get("GRAPH_OS_BIND_PORT", "8000")),
        public_base_url=source.get("GRAPH_OS_PUBLIC_BASE_URL", "http://localhost:8000"),
        deployment_environment=source.get(
            "GRAPH_OS_DEPLOYMENT_ENVIRONMENT", "development"
        ),
    )


def main() -> int:
    """CLI entry point: print the resolved hosting/deployment settings."""
    settings = load_hosting_deployment_settings()
    print(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
