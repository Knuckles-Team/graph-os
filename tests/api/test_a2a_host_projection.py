"""The MCP host binds A2A to its existing verified operation authority."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from graph_os.api.invoke.steps import VerifiedCaller
from graph_os.api.ops.fleet import operations
from graph_os.api.registry import Registry


def _caller(scopes: frozenset[str]) -> VerifiedCaller:
    return VerifiedCaller(
        principal="fixture:reader",
        tenant="fixture-tenant",
        effective_scopes=scopes,
        engine_claims={
            "principal": "fixture:reader",
            "tenant": "fixture-tenant",
            "scopes": sorted(scopes),
        },
        principal_kind="human",
    )


async def test_hosted_a2a_card_uses_bound_registry_caller_and_batched_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.a2a.mcp import _operation_projection
    from graph_os.mcp_server import runtime

    selected = tuple(
        op
        for op in operations()
        if op.id in {"fleet.catalog.search", "fleet.tools.load"}
    )
    registry = Registry(selected)
    calls: list[tuple[Any, ...]] = []

    class Gate:
        async def visible(self, items: Any, caller: VerifiedCaller) -> list[bool]:
            calls.append(tuple(item.resource for item in items))
            return [True] * len(items)

    caller = _caller(frozenset({"mcp:discover"}))
    services = SimpleNamespace(registry=registry)
    bound = SimpleNamespace(
        registry=registry,
        services=services,
        policy_gate=Gate(),
        caller_for_request=lambda: caller,
    )
    monkeypatch.setattr(runtime, "served_api", lambda: (bound, object()))

    projection = _operation_projection()
    assert projection._services is services
    assert tuple(op.id for op in await projection.visible_card_ops()) == (
        "fleet.catalog.search",
    )
    assert calls == [("op:fleet.catalog.search",)]


async def test_hosted_a2a_card_fails_closed_on_policy_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.a2a.mcp import _operation_projection
    from graph_os.mcp_server import runtime

    registry = Registry(op for op in operations() if op.id == "fleet.catalog.search")

    class MisalignedGate:
        async def visible(self, items: Any, caller: VerifiedCaller) -> list[bool]:
            return []

    bound = SimpleNamespace(
        registry=registry,
        services=SimpleNamespace(registry=registry),
        policy_gate=MisalignedGate(),
        caller_for_request=lambda: _caller(frozenset({"mcp:discover"})),
    )
    monkeypatch.setattr(runtime, "served_api", lambda: (bound, object()))
    assert await _operation_projection().visible_card_ops() == ()


async def test_hosted_card_advertises_only_authorized_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from graph_os.a2a.application import create_a2a_application
    from graph_os.a2a.mcp import _operation_projection
    from graph_os.a2a.service import A2AService
    from graph_os.mcp_server import runtime

    registry = Registry(op for op in operations() if op.id == "fleet.catalog.search")

    class Authenticator:
        async def authenticate(self, request: Any, *, scope: str) -> None:
            if request.headers.get("Authorization") != "Bearer verified":
                raise HTTPException(status_code=401)
            assert scope == ""

    class Gate:
        async def visible(self, items: Any, caller: VerifiedCaller) -> list[bool]:
            return [True] * len(items)

    for scopes in (frozenset({"mcp:discover"}), frozenset()):
        caller = _caller(scopes)
        bound = SimpleNamespace(
            registry=registry,
            services=SimpleNamespace(registry=registry),
            policy_gate=Gate(),
            caller_for_request=lambda selected=caller: selected,
        )
        monkeypatch.setattr(runtime, "served_api", lambda active=bound: (active, None))
        app = create_a2a_application(
            service=A2AService(authority=SimpleNamespace(), router=SimpleNamespace()),
            authenticator=Authenticator(),
            operation_projection=_operation_projection(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture.test"
        ) as client:
            assert (await client.get("/.well-known/agent-card.json")).status_code == 401
            response = await client.get(
                "/.well-known/agent-card.json",
                headers={"Authorization": "Bearer verified"},
            )
        assert response.status_code == 200
        card = response.json()
        assert {skill["id"] for skill in card["skills"]} == (
            {"fleet.catalog.search"} if scopes else set()
        )
        assert card["security"] == [{"bearerAuth": []}]


async def test_served_card_and_mcp_find_share_verified_session_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from graph_os.a2a.application import create_a2a_application
    from graph_os.a2a.mcp import _operation_projection
    from graph_os.a2a.service import A2AService
    from graph_os.api.mcp.discovery import visible_ops
    from graph_os.api.policy import PolicyGate
    from graph_os.api.policy.pdp_remote import EmbeddedPolicy, load_policy_file
    from graph_os.api.serving import caller_from_verified_session
    from graph_os.mcp_server import runtime

    registry = Registry(op for op in operations() if op.id == "fleet.catalog.search")

    class Actor:
        authenticated = True
        actor_type = "human"
        actor_id = "fixture:reader"

        def ensure_credential_current(self) -> None:
            pass

    class Session:
        actor = Actor()
        tenant = "fixture-tenant"
        policy_version = "fixture-revision"

        def __init__(self, scopes: frozenset[str]) -> None:
            self.scopes = scopes

        def ensure_authority_current(self) -> None:
            pass

        def engine_verified_context(self) -> dict[str, Any]:
            return {
                "principal": self.actor.actor_id,
                "tenant": self.tenant,
                "scopes": sorted(self.scopes),
                "policy_version": self.policy_version,
            }

    class Authenticator:
        async def authenticate(self, request: Any, *, scope: str) -> None:
            if request.headers.get("Authorization") != "Bearer verified":
                raise HTTPException(status_code=401)
            assert scope == ""

    seen: list[tuple[str, str]] = []
    policy = load_policy_file(
        str(Path(__file__).parent / "eunomia_fixtures/authority_policy.yaml")
    )

    class Gate:
        inner = PolicyGate("embedded", EmbeddedPolicy([policy]))

        async def visible(self, items: Any, caller: VerifiedCaller) -> list[bool]:
            seen.append((caller.principal, caller.policy_revision))
            return await self.inner.visible(items, caller)

    gate = Gate()
    for scopes in (frozenset({"mcp:discover"}), frozenset()):
        session = Session(scopes)

        @contextmanager
        def verified_scope(active: Session = session) -> Any:
            yield active

        monkeypatch.setattr(runtime, "verified_tool_session_scope", verified_scope)
        bound = SimpleNamespace(
            registry=registry,
            services=SimpleNamespace(registry=registry),
            policy_gate=gate,
            caller_for_request=caller_from_verified_session,
        )
        monkeypatch.setattr(runtime, "served_api", lambda active=bound: (active, None))
        app = create_a2a_application(
            service=A2AService(authority=SimpleNamespace(), router=SimpleNamespace()),
            authenticator=Authenticator(),
            operation_projection=_operation_projection(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture.test"
        ) as client:
            response = await client.get(
                "/.well-known/agent-card.json",
                headers={"Authorization": "Bearer verified"},
            )
        assert response.status_code == 200
        card_ids = {skill["id"] for skill in response.json()["skills"]}
        mcp_ids = {
            op.id
            for op in await visible_ops(registry, caller_from_verified_session(), gate)
        }
        assert card_ids == mcp_ids
    assert seen and set(seen) == {("fixture:reader", "fixture-revision")}
