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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .admission import AdmissionService
from .broker import IdentityBroker
from .engine import EngineIdentityPort, IdentityEngine, Resolution
from .exchange import UpstreamVerifier, exchange_routes
from .gate import IdentityGate
from .issuer import IssuerSettings, LocalIssuer, SecretStore
from .modes import (
    NoneModeRequestGuard,
    default_mode_for_profile,
    refuse_unsafe_none_mode,
)
from .principal_session import session_for
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

    @classmethod
    def from_settings(cls, config: Any) -> IdentityDeployment:
        profile = str(getattr(config, "deployment_profile", None) or "tiny")
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
        return cls(
            profile=profile,
            seed_mode=_setting("GRAPHOS_AUTH_MODE")
            or default_mode_for_profile(profile),
            none_ack=_setting("GRAPHOS_AUTH_NONE_EXPOSE"),
            none_hostname=_setting("GRAPHOS_AUTH_NONE_HOSTNAME"),
            setup_code=_setting("GRAPHOS_SETUP_CODE"),
            issuer=IssuerSettings(issuer=issuer, audience=audience, tenant=tenant),
        )


@dataclass(frozen=True)
class IdentityRuntime:
    """Everything one serving loop needs: broker, admission and routes."""

    deployment: IdentityDeployment
    broker: IdentityBroker
    admission: AdmissionService
    setup: SetupGate

    def routes(
        self,
        role_of: Callable[[Iterable[str]], str | None],
        upstream: Mapping[str, UpstreamVerifier] | None = None,
    ) -> list[Any]:
        return [
            *issuer_routes(self.admission),
            *session_routes(self.admission, self.setup, role_of),
            *factor_routes(self.admission),
            *exchange_routes(self.admission, upstream),
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
        return session_for(broker_ref(), service, ("service",))

    return session


def build_identity_runtime(
    deployment: IdentityDeployment,
    *,
    secrets: SecretStore,
    engine: IdentityEngine | None = None,
    client_for: Callable[[str], Any] | None = None,
    broker_session: Callable[[], Any] | None = None,
) -> IdentityRuntime:
    """Assemble the runtime; with no ``broker_session`` GraphOS self-mints it."""
    issuer = LocalIssuer(secrets, deployment.issuer)
    holder: list[IdentityBroker] = []
    if engine is None:
        if client_for is None:
            raise ValueError("an identity engine or a graph client factory is required")
        session_source = broker_session or self_minted_broker_session(lambda: holder[0])
        engine = EngineIdentityPort(client_for, session_source)
    broker = IdentityBroker(engine, issuer)
    holder.append(broker)
    admission = AdmissionService(broker, NoneModeRequestGuard(deployment.none_hostname))
    return IdentityRuntime(
        deployment, broker, admission, SetupGate(deployment.setup_code)
    )
