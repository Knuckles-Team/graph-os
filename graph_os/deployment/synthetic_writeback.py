"""GraphOS composition for the SDK's served synthetic D18 acceptance source.

The test namespace supplies a verified actor context, EG secret reference, MCP
credentials and lifecycle.  This module only joins the authenticated clients;
it never creates an EG grant, source change set, or connector identity.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agent_connector_sdk.credentials.resolution import resolve_secret_reference
from agent_connector_sdk.credentials.resolver import CredentialResolver
from agent_connector_sdk.ports.session import TransportEndpoint
from agent_connector_sdk.testing.synthetic_writeback import (
    LiveSyntheticWriteBackEvidence,
    run_live_synthetic_writeback_acceptance,
    seed_synthetic_writeback_test_source,
    serve_synthetic_writeback_test_namespace,
    verify_synthetic_writeback_source_effect,
)
from agent_connector_sdk.writeback.epistemic_graph import EpistemicGraphWriteBackLedger
from epistemic_graph import EpistemicGraphClient, validate_request_context
from epistemic_graph.generated.write_back import (
    SourceChangeSet,
    WriteBackAuthorizationMode,
)
from pydantic import JsonValue

from graph_os.connector_runner import (
    ConnectorRunnerConfig,
    ConnectorRunnerNotReadyError,
)
from graph_os.epistemic import ClientContext, ConnectClient, EpistemicClientPool

__all__ = [
    "RestartedWriteBackEvidence",
    "SyntheticWriteBackComposition",
    "WRITE_BACK_SCOPE",
]

WRITE_BACK_SCOPE = "connector:write-back"
_DEFAULT_CONNECT = cast(ConnectClient, EpistemicGraphClient.connect)


@dataclass(frozen=True, slots=True)
class RestartedWriteBackEvidence:
    """One EG receipt observed across two distinct served connector instances."""

    before_instance: str
    after_instance: str
    receipt: LiveSyntheticWriteBackEvidence


class SyntheticWriteBackComposition:
    """Bind SDK's test source to a tenant-granted, graph-bound EG client.

    Each method rechecks the caller's verified context before connection. The
    pool keeps transport ownership here, while request-local claims are scoped
    to the operation and never become a process-global credential.
    """

    def __init__(
        self,
        config: ConnectorRunnerConfig,
        *,
        credential_resolver: CredentialResolver,
        connect: ConnectClient = _DEFAULT_CONNECT,
        tls: bool = False,
        tls_server_hostname: str | None = None,
    ) -> None:
        if credential_resolver is None:
            raise TypeError("credential_resolver is required")
        auth_secret = resolve_secret_reference(
            config.auth_secret_ref, credential_resolver
        )
        if not auth_secret:
            raise ConnectorRunnerNotReadyError(
                "epistemic-graph authentication secret resolved empty"
            )
        if tls and (config.tcp_addr is None or not tls_server_hostname):
            raise ValueError("TLS write-back needs a TCP endpoint and server hostname")
        if tls_server_hostname and not tls:
            raise ValueError("TLS server hostname requires TLS")
        self._config = config
        tls_options: dict[str, object] = (
            {"tls": True, "tls_server_hostname": tls_server_hostname} if tls else {}
        )
        self._pool = EpistemicClientPool(
            connect,
            socket_path=config.socket_path,
            tcp_addr=config.tcp_addr,
            auth_secret=auth_secret,
            **tls_options,
        )

    @staticmethod
    def _require_context(context: ClientContext) -> None:
        if not isinstance(context, ClientContext):
            raise TypeError("synthetic write-back requires a verified ClientContext")
        context.require_scope(WRITE_BACK_SCOPE)
        validate_request_context(context.to_claims())

    async def serve(
        self,
        context: ClientContext,
        source_dir: Path,
        *,
        client_id: str,
        command_args: list[str],
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        """Serve until stopped by the test namespace's process supervisor."""
        self._require_context(context)
        async with self._pool.bind(
            context, self._config.graph, required_scope=WRITE_BACK_SCOPE
        ) as client:
            await serve_synthetic_writeback_test_namespace(
                client,
                source_dir,
                graph=self._config.graph,
                tenant_id=context.tenant,
                client_id=client_id,
                command_args=command_args,
                credential_resolver=credential_resolver,
            )

    async def probe(
        self,
        context: ClientContext,
        endpoint: TransportEndpoint,
        *,
        change_set_id: str,
        expected_mode: WriteBackAuthorizationMode,
        prior: LiveSyntheticWriteBackEvidence | None = None,
    ) -> LiveSyntheticWriteBackEvidence:
        """Probe the real MCP listener and durable EG receipt before/after restart."""
        self._require_context(context)
        async with self._pool.bind(
            context, self._config.graph, required_scope=WRITE_BACK_SCOPE
        ) as client:
            return await run_live_synthetic_writeback_acceptance(
                client,
                endpoint,
                graph=self._config.graph,
                tenant_id=context.tenant,
                change_set_id=change_set_id,
                expected_mode=expected_mode,
                prior=prior,
            )

    async def seed_source(
        self,
        context: ClientContext,
        source_dir: Path,
        *,
        change_set_id: str,
        expected_mode: WriteBackAuthorizationMode,
        initial_fields: dict[str, JsonValue],
    ) -> SourceChangeSet:
        """Seed a fresh source only from EG's already authorized change set."""
        self._require_context(context)
        async with self._pool.bind(
            context, self._config.graph, required_scope=WRITE_BACK_SCOPE
        ) as client:
            change = await EpistemicGraphWriteBackLedger(
                client, graph=self._config.graph
            ).get(context.tenant, change_set_id)
        if (
            change is None
            or change.tenant_id != context.tenant
            or change.change_set_id != change_set_id
            or not change.authorization.authorized
            or change.authorization.mode is not expected_mode
        ):
            raise ConnectorRunnerNotReadyError("EG write-back change is not granted")
        seed_synthetic_writeback_test_source(
            source_dir, change, initial_fields=initial_fields
        )
        return change

    async def verify_source_effect(
        self,
        context: ClientContext,
        source_dir: Path,
        evidence: LiveSyntheticWriteBackEvidence,
    ) -> None:
        """Read the EG change and disposable source independently after restart."""
        self._require_context(context)
        if evidence.tenant_id != context.tenant:
            raise ValueError("write-back evidence belongs to another tenant")
        async with self._pool.bind(
            context, self._config.graph, required_scope=WRITE_BACK_SCOPE
        ) as client:
            change = await EpistemicGraphWriteBackLedger(
                client, graph=self._config.graph
            ).get(context.tenant, evidence.change_set_id)
            if change is None:
                raise ConnectorRunnerNotReadyError(
                    "EG write-back change is unavailable"
                )
            await verify_synthetic_writeback_source_effect(source_dir, change, evidence)

    async def run_restarted_acceptance(
        self,
        context: ClientContext,
        endpoint: TransportEndpoint,
        source_dir: Path,
        *,
        change_set_id: str,
        expected_mode: WriteBackAuthorizationMode,
        server_instance: Callable[[], Awaitable[str]],
        restart_server: Callable[[], Awaitable[None]],
    ) -> RestartedWriteBackEvidence:
        """Probe apply, observe a real restart, replay, and check source state.

        The test namespace supplies a reader for the serving pod/process identity
        and a restart operation. A repeated or absent instance identity refuses
        success before replay; the caller retains both identities as evidence.
        """
        self._require_context(context)
        before = await server_instance()
        if not isinstance(before, str) or not before.strip():
            raise ConnectorRunnerNotReadyError(
                "served connector instance is unavailable"
            )
        first = await self.probe(
            context,
            endpoint,
            change_set_id=change_set_id,
            expected_mode=expected_mode,
        )
        await restart_server()
        after = await server_instance()
        if not isinstance(after, str) or not after.strip() or after == before:
            raise ConnectorRunnerNotReadyError(
                "served connector restart was not observed"
            )
        replay = await self.probe(
            context,
            endpoint,
            change_set_id=change_set_id,
            expected_mode=expected_mode,
            prior=first,
        )
        if replay.server_instance_id == first.server_instance_id:
            raise ConnectorRunnerNotReadyError(
                "MCP endpoint still serves the prior connector instance"
            )
        if (
            replay.model_copy(update={"server_instance_id": first.server_instance_id})
            != first
        ):
            raise ConnectorRunnerNotReadyError("write-back replay changed the receipt")
        await self.verify_source_effect(context, source_dir, replay)
        return RestartedWriteBackEvidence(before, after, replay)

    async def close(self) -> None:
        """Close owned EG transport after the namespace worker stops."""
        await self._pool.close()
