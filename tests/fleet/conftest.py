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

``_empty_local_skill_catalog_by_default`` isolates every test in this
directory from the local-skill source (CONCEPT:AU-KG.retrieval.unified-
capability-contract, ``graph_os.fleet.local_skill_catalog``): the real
``build_local_skill_catalog`` resolves whatever is actually installed in
THIS process (agent-utilities' own bundled skills, graph-os's own, any
sibling package declaring the same entry point, …), which would otherwise
leak into every ``discover_tools``/``_catalog_fleet_probe`` call
non-deterministically and couple this suite's results to the synced
environment's exact package set. Unlike ``stdio_fleet_authority`` this one
IS autouse, since no test in this directory wants that leakage; a test that
specifically exercises the merge (``tests/fleet/test_local_skill_catalog.py``,
``tests/fleet/test_local_skill_discovery.py``) overrides it with its own
``monkeypatch.setattr`` call after this fixture has already run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agent_utilities.security.brain_context import ActorContext, use_actor

import graph_os.fleet.multiplexer as _multiplexer_module

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


@pytest.fixture(autouse=True)
def _empty_local_skill_catalog_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _multiplexer_module, "build_local_skill_catalog", lambda: ([], [])
    )
