"""Put the identity broker in front of a served GraphOS listener.

A served listener (the dashboard co-service today) gets its identity gate from
here, in three steps that always run together:

1. :func:`served_identity_runtime` assembles the broker over the process
   engine, the configured secrets backend and the deployment's settings;
2. :func:`prepare_identity` seeds the store on first boot (``none`` for the
   tiny profile; production profiles wait for the first-run form), then
   refuses to serve ``none`` mode off loopback or on a production profile
   without the exact acknowledgement, prints the demo-mode warning block and
   starts the scheduled LDAP / AD group sync;
3. :func:`webui_session_boundary` hands agent-webui the gate through its
   host session-boundary port, so the WebUI's own identity layer only ever
   sees admitted local-issuer tokens.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Any

from .composition import IdentityDeployment, IdentityRuntime, build_identity_runtime
from .modes import NONE_MODE_BANNER
from .setup_gate import seed_first_boot

__all__ = ["prepare_identity", "served_identity_runtime", "webui_session_boundary"]

logger = logging.getLogger(__name__)

_NONE_MODE_WARNING = (
    "\n"
    "==================================================================\n"
    " GraphOS auth mode is 'none': %s.\n"
    " Secure this install: graph-os-identity claim\n"
    "==================================================================\n"
)


def served_identity_runtime() -> IdentityRuntime:
    """The broker over the process engine and the configured secrets backend."""
    from agent_utilities.core.config import config
    from agent_utilities.security.secrets_client import create_secrets_client

    from graph_os.mcp_server.bootstrap import graph_client

    return build_identity_runtime(
        IdentityDeployment.from_settings(config),
        secrets=create_secrets_client(),
        client_for=graph_client,
    )


async def prepare_identity(
    runtime: IdentityRuntime, bind_hosts: Iterable[str]
) -> str | None:
    """Seed on first boot and refuse an unsafe ``none`` exposure.

    Answers the stored mode, or ``None`` while production setup is owed (the
    first-run setup code is logged then).

    Raises:
        NoneModeExposureRefused: ``none`` would serve off loopback or on a
            production profile without the exact acknowledgement.
    """
    deployment = runtime.deployment
    runtime.refuse_unsafe_exposure(deployment.seed_mode, bind_hosts)
    mode = await seed_first_boot(runtime.admission, deployment.seed_mode)
    if mode is None:
        runtime.setup.announce()
    else:
        runtime.refuse_unsafe_exposure(mode, bind_hosts)
    if mode == "none":
        logger.warning(_NONE_MODE_WARNING, NONE_MODE_BANNER)
    # The scheduled LDAP / AD group sync: idle until an ldap IdP is enabled.
    runtime.background.append(asyncio.ensure_future(runtime.external.ldap_sync_loop()))
    return mode


def webui_session_boundary(runtime: IdentityRuntime) -> Callable[[Any], None]:
    """agent-webui's session-boundary port, backed by the identity gate.

    The WebUI role shown to a browser is derived by the WebUI's own server
    ladder from the admitted scopes; the frontend never derives one.
    """
    from agent_webui.rbac import resolve_webui_role

    def role_of(scopes: Iterable[str]) -> str | None:
        return resolve_webui_role(scopes, authenticated=True)

    def install(app: Any) -> None:
        runtime.install(app, role_of)

    return install
