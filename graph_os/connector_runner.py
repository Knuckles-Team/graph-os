"""Authenticated composition of agent-connector-sdk runners.

GraphOS owns deployment identity and the epistemic-graph transport.  The SDK
owns connector/source behavior and runner construction.  This module joins
those public contracts without manufacturing identity, copying an EG DTO, or
letting the SDK discover credentials from ambient process state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agent_connector_sdk.credentials.references import parse_secret_reference
from agent_connector_sdk.credentials.resolution import resolve_secret_reference
from agent_connector_sdk.credentials.resolver import CredentialResolver
from agent_connector_sdk.discovery import ActivationPolicy
from agent_connector_sdk.runner.composition import default_services
from agent_connector_sdk.runner.descriptors import RunnerSettings
from agent_connector_sdk.runner.services import RunnerServices
from agent_connector_sdk.sinks.epistemic_graph import PackImportAuthorityResolver
from epistemic_graph import (
    EpistemicGraphClient,
    RequestContextClaims,
    validate_request_context,
)

from graph_os.epistemic import ClientContext, ConnectClient, EpistemicClientPool

__all__ = [
    "ConnectorRunnerComposition",
    "ConnectorRunnerConfig",
    "ConnectorRunnerNotReadyError",
    "SOURCE_INGEST_SCOPE",
]

SOURCE_INGEST_SCOPE = "source:ingest"


class ConnectorRunnerNotReadyError(RuntimeError):
    """The authenticated connector runner cannot safely accept work."""


@dataclass(frozen=True, slots=True)
class ConnectorRunnerConfig:
    """Reference-only deployment configuration for one connector runner.

    Exactly one transport endpoint is required.  ``auth_secret_ref`` is parsed
    on construction but resolved only while GraphOS composes the live client;
    secret material is never part of this durable value.
    """

    graph: str
    state_dir: Path
    auth_secret_ref: str
    socket_path: str | None = None
    tcp_addr: str | None = None

    def __post_init__(self) -> None:
        if not self.graph.strip():
            raise ValueError("connector runner graph must be a non-empty string")
        if not isinstance(self.state_dir, Path):
            raise TypeError("connector runner state_dir must be a Path")
        if bool(self.socket_path) == bool(self.tcp_addr):
            raise ValueError(
                "connector runner requires exactly one epistemic-graph endpoint"
            )
        for name in ("socket_path", "tcp_addr"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"connector runner {name} must be non-empty")
        parse_secret_reference(self.auth_secret_ref)


ServicesFactory = Callable[..., RunnerServices]
_DEFAULT_CONNECT = cast(ConnectClient, EpistemicGraphClient.connect)


class ConnectorRunnerComposition:
    """Own the EG transport pool and inject its verified client into the SDK.

    The caller supplies a :class:`ClientContext` previously derived from a
    verified GraphOS actor.  Raw claim mappings are deliberately not accepted.
    One composition instance owns one client pool and must be closed at process
    shutdown.
    """

    def __init__(
        self,
        config: ConnectorRunnerConfig,
        *,
        credential_resolver: CredentialResolver,
        pack_import_authority: PackImportAuthorityResolver,
        connect: ConnectClient = _DEFAULT_CONNECT,
        services_factory: ServicesFactory = default_services,
    ) -> None:
        if credential_resolver is None:
            raise TypeError("credential_resolver is required")
        if not callable(pack_import_authority):
            raise TypeError("pack_import_authority is required")
        auth_secret = resolve_secret_reference(
            config.auth_secret_ref, credential_resolver
        )
        if not auth_secret:
            raise ConnectorRunnerNotReadyError(
                "epistemic-graph authentication secret resolved empty"
            )
        self._config = config
        self._pack_import_authority = pack_import_authority
        self._services_factory = services_factory
        self._pool = EpistemicClientPool(
            connect,
            socket_path=config.socket_path,
            tcp_addr=config.tcp_addr,
            auth_secret=auth_secret,
        )

    @staticmethod
    def _verified_claims(context: ClientContext) -> RequestContextClaims:
        if not isinstance(context, ClientContext):
            raise TypeError("connector runner requires a verified ClientContext")
        context.require_scope(SOURCE_INGEST_SCOPE)
        return validate_request_context(context.to_claims())

    @asynccontextmanager
    async def services(
        self,
        context: ClientContext,
        settings: RunnerSettings,
        *,
        policy: ActivationPolicy | None = None,
    ) -> AsyncIterator[RunnerServices]:
        """Yield SDK services bound to one authenticated EG client.

        Readiness is checked before the services escape the composition root.
        There is no unauthenticated, ``client=None``, alternate-sink, or ambient
        identity path.
        """

        self._verified_claims(context)
        async with self._pool.bind(
            context,
            self._config.graph,
            required_scope=SOURCE_INGEST_SCOPE,
        ) as client:
            services = self._services_factory(
                settings,
                state_dir=self._config.state_dir,
                sink_name="epistemic_graph",
                sink_client=client,
                pack_import_authority=self._pack_import_authority,
                policy=policy,
            )
            readiness = await services.sink.readiness()
            if not readiness.ready:
                reason = readiness.reason or "epistemic-graph sink is not ready"
                raise ConnectorRunnerNotReadyError(reason)
            yield services

    async def close(self) -> None:
        """Close every graph-bound EG client owned by this composition."""

        await self._pool.close()
