"""Shared pytest fixtures for ``tests/fleet/``.

``stdio_fleet_authority`` binds the verified local-process actor context that
``_require_fleet_capability`` now requires for stdio callers as much as any
other transport (GRAPHOS-FLEET-R021): a test that drives
``MCPMultiplexer``'s meta-tools or proxied dispatch directly, with no HTTP
request mocked, needs a concrete, authenticated capability set bound or the
check fails closed. Opt in explicitly per test
(``@pytest.mark.usefixtures("stdio_fleet_authority")``) rather than
autouse, so tests that specifically exercise the denied/unauthenticated path
keep seeing that path.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agent_utilities.security.brain_context import ActorContext, use_actor

# Covers every discover/delegate call these tests drive; no test in this
# directory currently needs the "manage"-kind operation, so no administrative
# scope is granted here.
_FLEET_STDIO_SCOPES = ("mcp:discover", "mcp:delegate")


@pytest.fixture
def stdio_fleet_authority() -> Iterator[None]:
    """Bind a verified local actor holding every fleet capability scope."""
    actor = ActorContext(
        actor_id="test-stdio-process",
        roles=_FLEET_STDIO_SCOPES,
        tenant_id="test-tenant",
        authenticated=True,
    )
    with use_actor(actor):
        yield
