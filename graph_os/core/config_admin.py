"""GraphOS-owned hosting/deployment configuration settings (GRAPHOS-DEPLOY-R009.1).

The former agent runtime's monolithic configuration module (``agent_utilities.
core.config``) mixed hosting/deployment settings in with connector-base and
agent/model settings. This module is GraphOS's own home for the
hosting/deployment slice — it resolves those settings from the environment
without importing the agent runtime's configuration internals.

Connector base settings belong to the connector SDK's own configuration
module; agent/model settings remain with the agent runtime. Neither is
imported here, by design — see the :func:`load_hosting_deployment_settings`
docstring and ``tests/core/test_config_admin.py`` for the import-boundary
test that enforces it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_VALID_PROFILES = frozenset({"dev", "test", "prod"})

_DEFAULT_HOST = "0.0.0.0"  # nosec B104 - documented serving-container bind-all default
_DEFAULT_PORT = 8085
_DEFAULT_PROFILE = "dev"
_DEFAULT_DATA_DIR = "/var/lib/graph-os"


class InvalidHostingDeploymentSettings(ValueError):
    """Raised when a resolved hosting/deployment setting fails validation."""


@dataclass(frozen=True, slots=True)
class HostingDeploymentSettings:
    """GraphOS's own hosting/deployment configuration settings.

    Attributes:
        host: Bind host for the serving container.
        port: Bind port for the serving container.
        deploy_profile: One of ``dev``, ``test``, ``prod`` (see
            ``GRAPHOS-DEPLOY-R013``'s development/production profiles).
        data_dir: Absolute path to GraphOS's persistent data directory.
    """

    host: str
    port: int
    deploy_profile: str
    data_dir: str


def load_hosting_deployment_settings(
    env: os._Environ[str] | dict[str, str] | None = None,
) -> HostingDeploymentSettings:
    """Resolve hosting/deployment settings from the environment.

    Reads ``GRAPHOS_HOST``, ``GRAPHOS_PORT``, ``GRAPHOS_DEPLOY_PROFILE`` and
    ``GRAPHOS_DATA_DIR``, falling back to GraphOS-owned defaults. This never
    imports ``agent_utilities.core.config`` (or any agent-runtime
    configuration internals) — the hosting/deployment slice is owned here,
    split out of that former monolithic module per ``GRAPHOS-DEPLOY-R009``.

    Raises:
        InvalidHostingDeploymentSettings: if the port is not a positive
            integer or the deploy profile is not one of ``dev``/``test``/
            ``prod``.
    """
    source = env if env is not None else os.environ

    host = str(source.get("GRAPHOS_HOST", _DEFAULT_HOST))

    raw_port = source.get("GRAPHOS_PORT", _DEFAULT_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise InvalidHostingDeploymentSettings(
            f"GRAPHOS_PORT must be an integer, got {raw_port!r}"
        ) from exc
    if port <= 0 or port > 65535:
        raise InvalidHostingDeploymentSettings(
            f"GRAPHOS_PORT must be in 1..65535, got {port}"
        )

    deploy_profile = str(source.get("GRAPHOS_DEPLOY_PROFILE", _DEFAULT_PROFILE))
    if deploy_profile not in _VALID_PROFILES:
        raise InvalidHostingDeploymentSettings(
            f"GRAPHOS_DEPLOY_PROFILE must be one of {sorted(_VALID_PROFILES)}, "
            f"got {deploy_profile!r}"
        )

    data_dir = str(source.get("GRAPHOS_DATA_DIR", _DEFAULT_DATA_DIR))

    return HostingDeploymentSettings(
        host=host,
        port=port,
        deploy_profile=deploy_profile,
        data_dir=data_dir,
    )
