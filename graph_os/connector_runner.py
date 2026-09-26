"""Authenticated composition of agent-connector-sdk runners.

GraphOS owns deployment identity and the epistemic-graph transport.  The SDK
owns connector/source behavior and runner construction.  This module joins
those public contracts without manufacturing identity, copying an EG DTO, or
letting the SDK discover credentials from ambient process state.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agent_connector_sdk.credentials.references import parse_secret_reference
from agent_connector_sdk.credentials.resolution import resolve_secret_reference
from agent_connector_sdk.credentials.resolver import CredentialResolver
from agent_connector_sdk.discovery import ActivationPolicy
from agent_connector_sdk.runner.cli import run_with_services
from agent_connector_sdk.runner.composition import default_services
from agent_connector_sdk.runner.descriptors import RunnerSettings
from agent_connector_sdk.runner.services import RunnerServices
from agent_connector_sdk.runner.static_registry import load_runner_config
from agent_connector_sdk.sinks.epistemic_graph import PackImportAuthorityResolver
from agent_utilities.knowledge_graph.core.session import GraphSession, resolve_session
from epistemic_graph import (
    EpistemicGraphClient,
    RequestContextClaims,
    validate_request_context,
)
from epistemic_graph.generated.connector_pack import (
    AgentLibraryMutationContext,
    McpCatalogSnapshotBinding,
)

from graph_os.epistemic import ClientContext, ConnectClient, EpistemicClientPool

__all__ = [
    "ConnectorRunnerComposition",
    "ConnectorRunnerConfig",
    "ConnectorRunnerNotReadyError",
    "SOURCE_INGEST_SCOPE",
    "compose_mounted_connector_runner",
    "run_connector_sync",
]

SOURCE_INGEST_SCOPE = "source:ingest"
_PACK_SCOPES = (
    "agent:pack-control",
    "agent:pack-read",
    "blob:write",
    "blob:read",
)
_OPAQUE_PRINCIPAL = re.compile(r"principal:sha256:[0-9a-f]{64}\Z")


def _caller_principal(principal: str) -> str:
    """Project the verified EG actor into AU's durable caller identity."""
    if _OPAQUE_PRINCIPAL.fullmatch(principal):
        return principal
    return "principal:sha256:" + hashlib.sha256(principal.encode("utf-8")).hexdigest()


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
        for scope in _PACK_SCOPES:
            context.require_scope(scope)
        return validate_request_context(context.to_claims())

    def _bound_pack_authority(
        self, context: ClientContext
    ) -> PackImportAuthorityResolver:
        """Validate each live binding against the verified runner identity."""

        async def resolve(
            connector: str,
        ) -> tuple[McpCatalogSnapshotBinding, AgentLibraryMutationContext]:
            result = await self._pack_import_authority(connector)
            if not isinstance(result, tuple) or len(result) != 2:
                raise ConnectorRunnerNotReadyError("pack authority is unavailable")
            catalog, mutation = result
            if not isinstance(catalog, McpCatalogSnapshotBinding) or not isinstance(
                mutation, AgentLibraryMutationContext
            ):
                raise ConnectorRunnerNotReadyError("pack authority is unavailable")
            if (
                catalog.configuration_revision < 1
                or catalog.catalog_generation < 1
                or catalog.child_connection_generation < 1
                or catalog.snapshot_digest == "0" * 64
                or catalog.authorization_scope_digest == "0" * 64
                or mutation.tenant_id != context.tenant
                or mutation.caller_principal != _caller_principal(context.principal)
            ):
                raise ConnectorRunnerNotReadyError(
                    "pack authority does not bind the verified runner"
                )
            return catalog, mutation

        return resolve

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
                pack_import_authority=self._bound_pack_authority(context),
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


def compose_mounted_connector_runner(
    config: ConnectorRunnerConfig,
    *,
    session: GraphSession,
    context: ClientContext,
    multiplexer: object,
    credential_resolver: CredentialResolver,
    connect: ConnectClient = _DEFAULT_CONNECT,
    services_factory: ServicesFactory = default_services,
) -> ConnectorRunnerComposition:
    """Bind an in-process SDK runner to this GraphOS fleet owner.

    The mounted authority is a Python callable and cannot cross into the
    separately deployed SDK-only connector-sync pod. This factory is usable
    only while the runner shares the GraphOS process, verified session, and
    mounted fleet lifecycle.
    """

    verified = resolve_session(session, required_scope=SOURCE_INGEST_SCOPE)
    if not isinstance(context, ClientContext):
        raise TypeError("connector runner requires a verified ClientContext")
    if (
        context.tenant != verified.tenant
        or context.principal != str(verified.actor.actor_id)
        or context.policy_version != str(verified.policy_version)
        or not set(context.scopes).issubset(verified.scopes)
    ):
        raise ConnectorRunnerNotReadyError(
            "runner EG identity differs from verified GraphOS session"
        )
    ready = getattr(multiplexer, "connector_pack_authority_ready", None)
    resolver = getattr(multiplexer, "resolve_connector_pack_authority", None)
    if not callable(ready) or not ready() or not callable(resolver):
        raise ConnectorRunnerNotReadyError(
            "mounted connector pack authority is unavailable"
        )
    return ConnectorRunnerComposition(
        config,
        credential_resolver=credential_resolver,
        pack_import_authority=resolver,
        connect=connect,
        services_factory=services_factory,
    )


async def run_connector_sync(
    config_path: Path,
    *,
    composition: ConnectorRunnerComposition,
    context: ClientContext,
    once: bool = False,
    log_format: str = "json",
    health_addr: str | None = None,
    health_allow_non_loopback: bool = False,
) -> int:
    """Run connector-sync with GraphOS's verified EG client and pack authority.

    Identity, credential resolution, endpoint and catalog authority must be
    supplied by the deployment. This function never reads them from ambient
    variables or manufactures a principal. The composition owns the client
    lifetime and is closed even when startup or a worker fails.
    """
    try:
        settings = load_runner_config(config_path).settings
        async with composition.services(context, settings) as services:
            argv = ["--config", str(config_path), "--log-format", log_format]
            if health_addr is not None:
                argv.extend(["--health-addr", health_addr])
            if health_allow_non_loopback:
                argv.append("--health-allow-non-loopback")
            if once:
                argv.append("--once")
            return await run_with_services(argv, services)
    finally:
        await composition.close()
