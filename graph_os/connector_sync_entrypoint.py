"""Authenticated GraphOS composition for the separately deployed connector runner.

The OIDC token is verified before it becomes EG request context. The same
refreshing client-credentials provider authenticates every remote GraphOS MCP
catalog request. A runner is restarted before that EG context's JWT expires.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import anyio
from agent_connector_sdk.auth.oidc import (
    ClientCredentialsConfig,
    client_credentials_auth,
)
from agent_connector_sdk.credentials.resolution import default_credential_resolver
from agent_connector_sdk.ports.session import TransportEndpoint
from agent_connector_sdk.runner.catalog_authority import (
    RemotePackImportAuthorityResolver,
)
from agent_connector_sdk.runner.composition import default_services
from agent_utilities.core.config import setting
from agent_utilities.security.request_identity import (
    mint_actor_from_token_sync,
    mint_graph_session,
)

from graph_os.connector_runner import (
    ConnectorRunnerComposition,
    ConnectorRunnerConfig,
    run_connector_sync,
)
from graph_os.epistemic import ClientContext

_MIN_REMAINING_SECONDS = 60.0
_RESTART_MARGIN_SECONDS = 45.0


def _required(name: str) -> str:
    value = str(setting(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _runner_config(state_dir: Path) -> ConnectorRunnerConfig:
    socket = str(setting("CONNECTOR_SYNC_EG_SOCKET_PATH", "")).strip() or None
    tcp = str(setting("CONNECTOR_SYNC_EG_TCP_ADDR", "")).strip() or None
    return ConnectorRunnerConfig(
        graph=_required("CONNECTOR_SYNC_GRAPH"),
        state_dir=state_dir,
        auth_secret_ref=_required("CONNECTOR_SYNC_EG_AUTH_SECRET_REF"),
        socket_path=socket,
        tcp_addr=tcp,
    )


def _verified_context(token: str) -> tuple[ClientContext, float, str]:
    actor = mint_actor_from_token_sync(token)
    session = mint_graph_session(actor)
    claims = session.engine_verified_context()
    context = ClientContext.from_actor(
        actor,
        tenant=session.tenant,
        audience=session.audience,
        scopes=session.scopes,
        policy_version=str(session.policy_version),
        oidc_token=token,
    )
    if (
        context.principal != claims["principal"]
        or context.tenant != claims["tenant"]
        or context.audience != claims["audience"]
    ):
        raise PermissionError("connector runner identity projection differs")
    expires_at = getattr(actor, "credential_expires_at", None)
    if not isinstance(expires_at, (int, float)):
        raise PermissionError("connector runner identity has no bounded expiry")
    return context, float(expires_at), session.graph


def _fleet_services_factory(auth, resolver):
    """Inject OIDC only for the connector's exact TLS fleet endpoint.

    Runner YAML stays secret-free. No service token is sent to a plain HTTP
    endpoint, arbitrary host, or redirect target selected by the descriptor.
    """

    def make(settings, **kwargs):
        services = default_services(settings, **kwargs)
        base_endpoints = services.endpoints

        def endpoint(descriptor):
            result = base_endpoints(descriptor)
            if not result.url:
                return result
            url = urlsplit(result.url)
            if (
                url.scheme != "https"
                or url.hostname != f"{descriptor.connector}.arpa"
                or url.port not in (None, 443)
                or url.path != "/mcp"
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise PermissionError(
                    "fleet MCP endpoint is outside TLS identity boundary"
                )
            if result.auth is not None or result.bearer_token:
                raise PermissionError(
                    "fleet MCP endpoint cannot override verified runner identity"
                )
            return replace(result, auth=auth)

        return replace(services, endpoints=endpoint)

    return make


async def _run(
    config_path: Path,
    state_dir: Path,
    *,
    once: bool,
    log_format: str,
    health_addr: str | None,
    health_allow_non_loopback: bool,
) -> int:
    resolver = default_credential_resolver()
    auth = client_credentials_auth(
        ClientCredentialsConfig.from_settings(), resolver=resolver
    )
    endpoint = TransportEndpoint(url=_required("GRAPH_OS_MCP_URL"), auth=auth)
    config = _runner_config(state_dir)
    while True:
        # A fresh verified actor is built from the provider's actual bearer.
        # The EG client receives this same token; MCP refreshes from its provider.
        token = await anyio.to_thread.run_sync(auth.provider.get_token)
        context, expires_at, verified_graph = _verified_context(token)
        # Graph names can carry a deployment prefix. The verified GraphOS
        # session is the authority for tenant-local naming, not a string match
        # against the bare tenant id.
        if config.graph != verified_graph:
            raise PermissionError("EG graph differs from verified tenant graph")
        lifetime = expires_at - time.time() - _RESTART_MARGIN_SECONDS
        if lifetime < _MIN_REMAINING_SECONDS:
            token = await anyio.to_thread.run_sync(
                lambda: auth.provider.get_token(force=True)
            )
            context, expires_at, verified_graph = _verified_context(token)
            if config.graph != verified_graph:
                raise PermissionError("EG graph differs from verified tenant graph")
            lifetime = expires_at - time.time() - _RESTART_MARGIN_SECONDS
        if lifetime < _MIN_REMAINING_SECONDS:
            raise PermissionError("runner token lifetime is too short")
        authority = RemotePackImportAuthorityResolver(
            endpoint, tenant=context.tenant, principal=context.principal
        )
        composition = ConnectorRunnerComposition(
            config,
            credential_resolver=resolver,
            pack_import_authority=authority,
            services_factory=_fleet_services_factory(auth, resolver),
        )
        if once:
            with anyio.move_on_after(lifetime) as deadline:
                result = await run_connector_sync(
                    config_path,
                    composition=composition,
                    context=context,
                    once=True,
                    log_format=log_format,
                    health_addr=health_addr,
                    health_allow_non_loopback=health_allow_non_loopback,
                )
            if deadline.cancel_called:
                raise PermissionError("runner cycle exceeded verified token lifetime")
            return result
        with anyio.move_on_after(lifetime) as deadline:
            result = await run_connector_sync(
                config_path,
                composition=composition,
                context=context,
                log_format=log_format,
                health_addr=health_addr,
                health_allow_non_loopback=health_allow_non_loopback,
            )
        if not deadline.cancel_called:
            return result
        # Reconnect with a newly verified JWT and EG client. Never reuse a
        # process-local context after its credential lease expires.


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-os-connector-sync")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-format", choices=("json", "text"), default="json")
    parser.add_argument("--health-addr")
    parser.add_argument("--health-allow-non-loopback", action="store_true")
    args = parser.parse_args(argv)
    state_dir = args.state_dir or Path(
        str(setting("CONNECTOR_SYNC_STATE_DIR", "/var/lib/connector-sync"))
    )

    async def run() -> int:
        return await _run(
            args.config,
            state_dir,
            once=args.once,
            log_format=args.log_format,
            health_addr=args.health_addr,
            health_allow_non_loopback=args.health_allow_non_loopback,
        )

    try:
        return anyio.run(run)
    except Exception as exc:
        # Startup errors are fixed-category on stdout/stderr; token and
        # provider exception text may contain sensitive metadata.
        import logging

        logging.getLogger(__name__).error(
            "connector-sync authenticated composition is unavailable: %s",
            type(exc).__name__,
        )
        return 2


__all__ = ["main"]
