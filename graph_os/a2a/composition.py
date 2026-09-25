"""Composition root for the GraphOS A2A facade."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_utilities.api import AgentControlPlane, AgentControlPlaneUnavailable

from graph_os.decide import current_decide

from .application import A2AAuthenticator, create_a2a_application
from .authority import WorkItemA2AAuthority
from .decide_routing import DecideA2ARouter
from .routing import ControlPlaneA2ARouter
from .service import A2ACardMetadata, A2AService

__all__ = [
    "A2AComposition",
    "compose_a2a",
    "compose_a2a_service",
    "hosted_control_plane",
]

GraphClientFor = Callable[[str], Any]


@dataclass(frozen=True)
class A2AComposition:
    application: Any
    service: A2AService


def hosted_control_plane(graph_client_for: GraphClientFor) -> Callable[[Any], Any]:
    """Bind AU's hosted control plane to the caller's tenant EG client.

    AU composes its own execution runner, signed dispatch and admission
    digests behind ``compose_hosted_agent_control_plane``; GraphOS supplies
    only the session-routed EG client. Until AU publishes that port every
    control-plane operation fails closed.
    """

    def control_plane_for(session: Any) -> AgentControlPlane:
        try:
            from agent_utilities.api import compose_hosted_agent_control_plane
        except ImportError as exc:
            raise AgentControlPlaneUnavailable(
                "AU hosted agent control-plane port is not published"
            ) from exc
        return compose_hosted_agent_control_plane(
            graph_client_for(str(session.tenant)), session
        )

    return control_plane_for


def compose_a2a_service(
    *,
    control_plane_for: Callable[[Any], Any],
    card_metadata: A2ACardMetadata | None = None,
) -> A2AService:
    """One A2A service over the control plane and EG assembly (via Decide)."""
    return A2AService(
        authority=WorkItemA2AAuthority(control_plane_for),
        router=DecideA2ARouter(
            ControlPlaneA2ARouter(control_plane_for), current_decide
        ),
        card_metadata=card_metadata or A2ACardMetadata(),
    )


def compose_a2a(
    *,
    control_plane_for: Callable[[Any], Any],
    authenticator: A2AAuthenticator,
    card_metadata: A2ACardMetadata | None = None,
) -> A2AComposition:
    """Compose the standalone A2A application over the shared service."""
    service = compose_a2a_service(
        control_plane_for=control_plane_for,
        card_metadata=card_metadata,
    )
    return A2AComposition(
        application=create_a2a_application(
            service=service,
            authenticator=authenticator,
        ),
        service=service,
    )
