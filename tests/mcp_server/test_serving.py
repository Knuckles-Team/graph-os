"""GraphOS host policy over the SDK MCP server factory."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.api import SessionRequiredError, current_session

from graph_os.mcp_server import serving


class _Session:
    def __init__(self, actor: Any) -> None:
        self.actor = actor

    def engine_verified_context(self) -> dict[str, str]:
        return {"tenant": "t1"}


def _context(claims: dict[str, Any] | None) -> Any:
    return SimpleNamespace(auth=SimpleNamespace(claims=claims))


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: None)


async def test_call_without_verified_identity_is_refused() -> None:
    middleware = serving.VerifiedSessionMiddleware(lambda: None)

    async def call_next(_context: Any) -> str:
        raise AssertionError("an unauthenticated call must not run")

    with pytest.raises(SessionRequiredError):
        await middleware.on_call_tool(_context(None), call_next)


async def test_stdio_process_authority_admits_the_call() -> None:
    middleware = serving.VerifiedSessionMiddleware(lambda: object())

    async def call_next(_context: Any) -> str:
        return "ran"

    assert await middleware.on_call_tool(_context(None), call_next) == "ran"


async def test_validated_claims_bind_the_caller_session(monkeypatch) -> None:
    import agent_utilities.security.request_identity as identity
    from agent_utilities.security.actor_identity import ActorType
    from agent_utilities.security.brain_context import ActorContext

    actor = ActorContext(
        actor_id="user-a",
        actor_type=ActorType.HUMAN,
        tenant_id="t1",
        authenticated=True,
    )
    minted = _Session(actor)
    monkeypatch.setattr(identity, "actor_from_claims", lambda claims: actor)
    monkeypatch.setattr(identity, "mint_graph_session", lambda value: minted)
    monkeypatch.setattr("agent_utilities.api.use_session", _fake_use)
    seen: list[Any] = []

    async def call_next(_context: Any) -> str:
        seen.append(_BOUND[-1] if _BOUND else None)
        return "ran"

    middleware = serving.VerifiedSessionMiddleware(lambda: None)
    result = await middleware.on_call_tool(_context({"sub": "user-a"}), call_next)

    assert result == "ran"
    assert seen == [minted]
    assert current_session() is None


_BOUND: list[Any] = []


@contextlib.contextmanager
def _fake_use(session: Any) -> Iterator[None]:
    _BOUND.append(session)
    try:
        yield
    finally:
        _BOUND.pop()


def test_remote_metrics_route_requires_a_resolved_token(monkeypatch) -> None:
    routes: list[str] = []

    def _record_route(path, methods):
        def _decorate(fn):
            routes.append(path)
            return fn

        return _decorate

    fake = SimpleNamespace(custom_route=_record_route)
    monkeypatch.delenv("MCP_METRICS_TOKEN_REF", raising=False)

    serving.register_metrics_route(fake, transport="streamable-http", host="0.0.0.0")
    assert routes == []

    serving.register_metrics_route(fake, transport="streamable-http", host="127.0.0.1")
    assert routes == ["/metrics"]
