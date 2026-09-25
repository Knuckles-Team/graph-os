"""Every external identity authority, assembled once (IDM-12..15, IDM-19).

The operator ruling (2026-09-24) is that external providers plug in: OIDC in
general (Keycloak, Okta, Entra ID, Google, ...), native SAML 2.0, and LDAP /
Active Directory groups, plus SCIM provisioning and optional e-mail. Which of
them a deployment uses is data -- the engine's enabled ``IdpConfig`` rows --
not code: the routes below are always mounted, and an IdP kind with no enabled
row simply offers nothing.

:class:`ExternalAuthorities` is what the GraphOS identity composition mounts
(``CONTRACT-REQUEST.md`` B): its :meth:`~ExternalAuthorities.routes`, its
background :meth:`~ExternalAuthorities.ldap_sync_loop`, and its
:attr:`~ExternalAuthorities.notifier`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from graph_os.identity.idp_common import (
    NO_STORE,
    EngineLoginCompleter,
    IdentityPort,
    IdpDirectory,
    LoginCompleter,
    OneShotBackend,
    OneShotStore,
    login_options,
)
from graph_os.identity.ldap import (
    DirectoryFactory,
    FailureThrottle,
    LdapBroker,
    ldap3_directory_factory,
)
from graph_os.identity.ldap_sync import LdapSync
from graph_os.identity.oidc import OidcBroker, ProviderCache
from graph_os.identity.saml import SamlBroker
from graph_os.identity.scim import ProvisionerAuth, ScimServer
from graph_os.identity.smtp import IdentityNotifier, notifier_from_settings

__all__ = ["ExternalAuthorities", "mail_settings_from_env"]

_MAIL_KEYS = (
    "GRAPHOS_SMTP_HOST",
    "GRAPHOS_SMTP_PORT",
    "GRAPHOS_SMTP_SECURITY",
    "GRAPHOS_SMTP_USERNAME",
    "GRAPHOS_SMTP_PASSWORD_REF",
    "GRAPHOS_MAIL_FROM",
    "GRAPHOS_LISTMONK_URL",
    "GRAPHOS_LISTMONK_USER",
    "GRAPHOS_LISTMONK_TOKEN_REF",
    "GRAPHOS_LISTMONK_TEMPLATES",
)


def mail_settings_from_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The ``GRAPHOS_SMTP_*`` / ``GRAPHOS_LISTMONK_*`` settings that are set."""
    source = os.environ if environ is None else environ
    return {key: source[key] for key in _MAIL_KEYS if source.get(key)}


@dataclass(frozen=True)
class ExternalAuthorities:
    """The OIDC, SAML and LDAP brokers, the SCIM server, the LDAP sync and
    the mail notifier over one engine port and one secrets backend."""

    directory: IdpDirectory
    oidc: OidcBroker
    saml: SamlBroker
    ldap: LdapBroker
    ldap_sync: LdapSync
    scim: ScimServer
    notifier: IdentityNotifier

    @classmethod
    def build(
        cls,
        *,
        port: IdentityPort,
        secrets: OneShotBackend,
        provisioners: ProvisionerAuth,
        mail_settings: Mapping[str, Any] | None = None,
        completer: LoginCompleter | None = None,
        directories: DirectoryFactory | None = None,
        providers: ProviderCache | None = None,
    ) -> ExternalAuthorities:
        directory = IdpDirectory(port)
        completer = completer or EngineLoginCompleter(port)
        directories = directories or ldap3_directory_factory(secrets)
        return cls(
            directory=directory,
            oidc=OidcBroker(
                port=port,
                directory=directory,
                transactions=OneShotStore(secrets, "oidc-tx"),
                completer=completer,
                secrets=secrets,
                providers=providers,
            ),
            saml=SamlBroker(
                directory=directory,
                transactions=OneShotStore(secrets, "saml-tx"),
                replay=OneShotStore(secrets, "saml-replay"),
                completer=completer,
            ),
            ldap=LdapBroker(
                directory=directory,
                directories=directories,
                completer=completer,
                throttle=FailureThrottle(secrets),
            ),
            ldap_sync=LdapSync(port=port, directory=directory, directories=directories),
            scim=ScimServer(auth=provisioners, directory=directory),
            notifier=notifier_from_settings(
                mail_settings_from_env() if mail_settings is None else mail_settings,
                secrets,
            ),
        )

    def features(self) -> dict[str, bool]:
        """The optional features a page may offer (e-mail today)."""
        return self.notifier.features()

    async def sign_in_options(self, request: Request) -> Response:
        """``GET /auth/idps``: enabled browser IdPs (optionally routed by an
        e-mail's domain) and which e-mail features exist."""
        email = request.query_params.get("email")
        records = await self.directory.records()
        body = {
            "idps": login_options(
                records, email if email and len(email) <= 320 else None
            ),
            "features": self.features(),
        }
        return JSONResponse(body, headers=NO_STORE)

    def routes(self) -> list[Route]:
        own = [Route("/auth/idps", self.sign_in_options, methods=["GET"])]
        brokers = [*self.oidc.routes(), *self.saml.routes(), *self.ldap.routes()]
        return [*own, *brokers, *self.scim.routes()]

    async def ldap_sync_loop(self) -> None:
        """The scheduled LDAP / AD group sync (run in the serving lifespan)."""
        await self.ldap_sync.run_forever()
