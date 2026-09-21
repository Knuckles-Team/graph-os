"""Composition root for the GraphOS unary A2A facade."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .application import A2AAuthenticator, create_a2a_application
from .authority import WorkItemA2AAuthority
from .routing import OrchestratorA2ARouter
from .service import A2ACardMetadata, A2AService

__all__ = ["A2AComposition", "compose_a2a"]


@dataclass(frozen=True)
class A2AComposition:
    application: Any
    service: A2AService


def compose_a2a(
    *,
    engine_provider: Callable[[], Any],
    authenticator: A2AAuthenticator,
    card_metadata: A2ACardMetadata | None = None,
) -> A2AComposition:
    """Compose one projection over the existing WorkItem/dispatch authorities."""
    service = A2AService(
        authority=WorkItemA2AAuthority(engine_provider),
        router=OrchestratorA2ARouter(engine_provider),
        card_metadata=card_metadata or A2ACardMetadata(),
    )
    return A2AComposition(
        application=create_a2a_application(
            service=service,
            authenticator=authenticator,
        ),
        service=service,
    )
