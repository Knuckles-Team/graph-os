"""Shared fleet test authority (EH-629).

Fleet operations require the caller's exact ``mcp:discover`` / ``mcp:delegate``
scopes; with no HTTP request the caller is the verified session bound to the
context (a stdio server's minted process session). Behaviour tests that drive
the multiplexer as an authorized local caller opt in with
``pytestmark = pytest.mark.usefixtures("fleet_scopes")``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from agent_utilities.api import GraphSession, use_session
from agent_utilities.security.brain_context import ActorContext

FLEET_SCOPES = ("mcp:discover", "mcp:delegate")


@contextmanager
def fleet_session(*roles: str) -> Iterator[GraphSession]:
    """Bind a verified local session holding exactly ``roles``."""
    session = GraphSession(
        actor=ActorContext(
            actor_id="fleet-test",
            roles=tuple(roles),
            tenant_id="fleet-test",
            authenticated=True,
        ),
        tenant="fleet-test",
    )
    with use_session(session):
        yield session


@pytest.fixture
def fleet_scopes() -> Iterator[GraphSession]:
    """The local caller holds both exact fleet scopes."""
    with fleet_session(*FLEET_SCOPES) as session:
        yield session
