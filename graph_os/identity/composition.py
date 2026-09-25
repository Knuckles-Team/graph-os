"""Compose the identity broker into a served GraphOS application.

One broker per serving event loop: the engine port, the persistent issuer
over the configured secrets backend, the admission service and the identity
gate. The deployment inputs are read once from the shared settings model:

``GRAPHOS_AUTH_MODE``
    First-boot seed only (profile default otherwise: ``none`` for tiny,
    ``local`` for production profiles). The stored mode always wins after.
``GRAPHOS_AUTH_NONE_EXPOSE`` / ``GRAPHOS_AUTH_NONE_HOSTNAME``
    The exact acknowledgement that lets ``none`` mode leave loopback, and the
    one extra host name it then answers to.
``GRAPHOS_SETUP_CODE``
    The first-run setup code (generated and logged when unset).
``GRAPHOS_IDENTITY_ISSUER`` / ``GRAPHOS_IDENTITY_TENANT``
    The issuer URL the engine and clients trust, and the engine tenant tokens
    carry. Both are required outside the tiny profile.
``GRAPHOS_CONSOLE_ORIGIN``
    Exact HTTPS browser origin (or loopback HTTP origin) allowed to request
    attended console operations. Without it, console mutations fail closed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .admission import AdmissionService
from .broker import IdentityBroker
from .engine import EngineIdentityPort, IdentityEngine, Resolution
from .engine_ports import BrokerPort, CallerPort
from .exchange import UpstreamVerifier, exchange_routes
from .external import ExternalAuthorities
from .gate import IdentityGate
from .idp_common import OneShotBackend
from .issuer import IssuerSettings, LocalIssuer
from .modes import (
    NoneModeRequestGuard,
    default_mode_for_profile,
    refuse_unsafe_none_mode,
)
from .principal_session import session_for
from .scim import ApiKeyProvisionerAuth
from .setup_gate import SetupGate
from .web_factors import factor_routes, issuer_routes
from .web_session import session_routes

__all__ = [
    "BROKER_PRINCIPAL",
    "IdentityDeployment",
    "IdentityRuntime",
    "build_identity_runtime",
    "self_minted_broker_session",
]

#: The service principal GraphOS brokers as when it mints its own authority.
BROKER_PRINCIPAL = "svc:graph-os"
_BROKER_SCOPES = frozenset({"identity:authenticate"})
_TINY_ISSUER = "http://127.0.0.1:8080"
_TINY_TENANT = "local"
_DEFAULT_AUDIENCE = "graph-os-local"


def _setting(name: str) -> str | None:
    from agent_utilities.core.config import setting

    value = str(setting(name, "") or "").strip()
    return value or None


@dataclass(frozen=True)
class IdentityDeployment:
    """The deployment's identity inputs, read once."""

    profile: str
    seed_mode: str
    none_ack: str | None
    none_hostname: str | None
    setup_code: str | None
    issuer: IssuerSettings
    console_origin: str | None = None

    @classmethod
    def from_settings(cls, config: Any) -> IdentityDeployment:
        profile = str(getattr(config, "deployment_profile", None) or "tiny")
        console_origin = _setting("GRAPHOS_CONSOLE_ORIGIN")
        if console_origin is not None:
            parsed = urlsplit(console_origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or console_origin != f"{parsed.scheme}://{parsed.netloc}"
                or parsed.username is not None
                or parsed.password is not None
                or (
                    parsed.scheme == "http"
                    and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                )
            ):
                raise ValueError(
                    "GRAPHOS_CONSOLE_ORIGIN must be an HTTPS origin or loopback HTTP origin"
                )
        return cls(
            profile=profile,
            seed_mode=_setting("GRAPHOS_AUTH_MODE")
            or default_mode_for_profile(profile),
            none_ack=_setting("GRAPHOS_AUTH_NONE_EXPOSE"),
            none_hostname=_setting("GRAPHOS_AUTH_NONE_HOSTNAME"),
            setup_code=_setting("GRAPHOS_SETUP_CODE"),
            issuer=_issuer_settings(profile, config),
            console_origin=console_origin,
        )


def _issuer_settings(profile: str, config: Any) -> IssuerSettings:
    """Issuer URL and tenant (tiny defaults to loopback); audience from config."""
    tiny = profile == "tiny"
    issuer = _setting("GRAPHOS_IDENTITY_ISSUER") or (_TINY_ISSUER if tiny else None)
    tenant = _setting("GRAPHOS_IDENTITY_TENANT") or (_TINY_TENANT if tiny else None)
    if not issuer or not tenant:
        raise RuntimeError(
            "GRAPHOS_IDENTITY_ISSUER and GRAPHOS_IDENTITY_TENANT are required "
            f"on the {profile!r} profile"
        )
    audience = str(
        getattr(config, "auth_jwt_audience", None)
        or getattr(config, "mcp_jwt_audience", None)
        or _DEFAULT_AUDIENCE
    )
    return IssuerSettings(issuer=issuer, audience=audience, tenant=tenant)


@dataclass(frozen=True)
class IdentityRuntime:
    """Everything one serving loop needs: broker, admission and routes."""

    deployment: IdentityDeployment
    broker: IdentityBroker
    admission: AdmissionService
    setup: SetupGate
    external: ExternalAuthorities
    #: Background tasks the serving loop started (held so they are not
    #: garbage-collected mid-run; cancelled with the loop).
    background: list[Any] = field(default_factory=list)

    def routes(
        self,
        role_of: Callable[[Iterable[str]], str | None],
        upstream: Mapping[str, UpstreamVerifier] | None = None,
    ) -> list[Any]:
        return [
            *issuer_routes(self.admission),
            *session_routes(
                self.admission, self.setup, role_of, self.external.features
            ),
            *factor_routes(self.admission),
            *exchange_routes(self.admission, upstream),
            *self.external.routes(),
        ]

    def refuse_unsafe_exposure(self, mode: str, bind_hosts: Iterable[str]) -> None:
        refuse_unsafe_none_mode(
            mode=mode,
            profile=self.deployment.profile,
            bind_hosts=bind_hosts,
            ack=self.deployment.none_ack,
        )

    def install(self, app: Any, role_of: Callable[[Iterable[str]], str | None]) -> None:
        """Install the identity gate as ``app``'s outermost identity layer."""
        app.add_middleware(
            IdentityGate, admission=self.admission, routes=self.routes(role_of)
        )


def self_minted_broker_session(
    broker_ref: Callable[[], IdentityBroker],
    *,
    process_key: bool = False,
) -> Callable[[], Any]:
    """GraphOS's broker authority minted by its own issuer (tiny profile).

    With no external identity provider the local issuer IS the authority, so
    GraphOS signs its own service token carrying exactly the broker scope. A
    fresh token is minted per call (they live five minutes).
    """
    service = Resolution(
        principal_id=BROKER_PRINCIPAL,
        username="graph-os",
        kind="service",
        status="active",
        scopes=_BROKER_SCOPES,
    )

    def session() -> Any:
        return session_for(
            broker_ref(),
            service,
            ("process" if process_key else "service",),
            process_key=process_key,
        )

    return session


def _external_authorities(
    engine: IdentityEngine, broker: IdentityBroker, secrets: OneShotBackend
) -> ExternalAuthorities:
    """OIDC/SAML/LDAP brokers, SCIM and mail over the same engine port.

    A SCIM provisioner acts under its OWN service principal: its API key is
    resolved to the key's current authority and the provisioning ops run
    under that principal's verified session, never GraphOS's.
    """

    def provisioner_port(resolution: Resolution) -> CallerPort:
        return CallerPort(engine, session_for(broker, resolution, ("api_key",)))

    return ExternalAuthorities.build(
        port=BrokerPort(engine),
        secrets=secrets,
        provisioners=ApiKeyProvisionerAuth(broker.verify_api_key, provisioner_port),
    )


def build_identity_runtime(
    deployment: IdentityDeployment,
    *,
    secrets: OneShotBackend,
    engine: IdentityEngine | None = None,
    client_for: Callable[[str], Any] | None = None,
    broker_session: Callable[[], Any] | None = None,
) -> IdentityRuntime:
    """Assemble the runtime; with no ``broker_session`` GraphOS self-mints it."""
    issuer = LocalIssuer(
        secrets, deployment.issuer, process_key_enabled=deployment.profile == "tiny"
    )
    holder: list[IdentityBroker] = []
    if engine is None:
        if client_for is None:
            raise ValueError("an identity engine or a graph client factory is required")
        session_source = broker_session or self_minted_broker_session(
            lambda: holder[0], process_key=deployment.profile == "tiny"
        )
        engine = EngineIdentityPort(client_for, session_source)
    broker = IdentityBroker(engine, issuer)
    holder.append(broker)
    admission = AdmissionService(broker, NoneModeRequestGuard(deployment.none_hostname))
    return IdentityRuntime(
        deployment,
        broker,
        admission,
        SetupGate(deployment.setup_code),
        _external_authorities(engine, broker, secrets),
    )
