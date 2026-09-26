"""The GraphOS registry reads EG under current caller and broker authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from graph_os.fleet import multiplexer
from graph_os.fleet.remote_oauth_broker import (
    OAuthGrantBinding,
    OAuthTokenAbsentError,
    ProviderDescriptor,
    ProviderRegistry,
    RemoteOAuthBroker,
    StoredToken,
)
from graph_os.gateway import registry_api


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/registry/servers",
            "query_string": b"",
            "headers": [],
        }
    )


def test_grant_fingerprint_matches_eg_discovery_schema() -> None:
    binding = OAuthGrantBinding(
        tenant_id="tenant-a",
        principal_id="caller-a",
        provider_id="provider-a",
        resource_url="https://provider.example/mcp",
        audience="https://provider.example/mcp",
        granted_scopes=("mcp:read",),
        key_version=1,
        grant_revision="revision-a",
    )
    material = {
        "schema": "au.oauth-grant-binding.v1",
        "tenant": "tenant-a",
        "principal": "caller-a",
        "provider": "provider-a",
        "resource": "https://provider.example/mcp",
        "audience": "https://provider.example/mcp",
        "scopes": ["mcp:read"],
        "key_version": 1,
        "grant_revision": "revision-a",
    }
    expected = hashlib.sha256(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    assert binding.fingerprint == expected
    assert (
        binding.fingerprint
        != OAuthGrantBinding(
            **{**binding.__dict__, "grant_revision": "revision-b"}
        ).fingerprint
    )


def test_current_fleet_grant_disappears_after_revocation(monkeypatch) -> None:
    class _Secrets:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str, **metadata) -> None:
            self.values[key] = value

    actor = ActorContext(
        actor_id="caller-a",
        actor_type=ActorType.HUMAN,
        tenant_id="tenant-a",
        authenticated=True,
        roles=("kg:read",),
    )
    provider = ProviderDescriptor(
        provider_id="provider-a",
        resource_url="https://provider.example/mcp",
        client_id="client-a",
        redirect_uri="https://broker.example/oauth/callback",
        scopes=("mcp:read",),
        enabled=True,
    )
    registry = ProviderRegistry()
    registry.register(provider)
    broker = RemoteOAuthBroker(registry=registry, secrets_client=_Secrets())
    broker.tokens.put(
        actor=actor,
        provider_id=provider.provider_id,
        resource_url=provider.resource_url,
        audience=provider.resource_url,
        token=StoredToken(
            access_token="synthetic-access",
            refresh_token=None,
            token_type="Bearer",
            expires_at=time.time() + 300,
            granted_scope="mcp:read",
            key_version=1,
            audience=provider.resource_url,
            grant_revision="revision-a",
        ),
    )
    monkeypatch.setattr(
        multiplexer, "_REMOTE_OAUTH_BROKERS", {provider.provider_id: broker}
    )
    current = multiplexer.current_remote_oauth_grant_bindings(actor)
    assert len(current) == 1
    assert isinstance(current[0], OAuthGrantBinding)
    assert current[0].grant_revision == "revision-a"
    from graph_os.gateway import remote_oauth_api

    monkeypatch.setattr(remote_oauth_api, "_get_broker", lambda: broker)
    assert registry_api._resolve_current_discovery_grants(actor) == (
        current[0].fingerprint,
    )
    monkeypatch.setattr(multiplexer, "_REMOTE_OAUTH_BROKERS", {})
    assert registry_api._resolve_current_discovery_grants(actor) == (
        current[0].fingerprint,
    )
    broker.revoke(actor=actor, provider_id=provider.provider_id)
    assert multiplexer.current_remote_oauth_grant_bindings(actor) == ()
    assert registry_api._resolve_current_discovery_grants(actor) == ()
    with pytest.raises(OAuthTokenAbsentError):
        broker.grant_binding_for(
            actor=actor,
            provider_id=provider.provider_id,
            resource_url=provider.resource_url,
        )


def test_registry_page_uses_graphos_engine_port_and_mounts_get_only(
    monkeypatch,
) -> None:
    calls: list[tuple[int, object]] = []

    class _Servers:
        def page(self, *, limit: int, cursor: object) -> SimpleNamespace:
            calls.append((limit, cursor))
            return SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        name="svc-a",
                        url="https://svc.example/secret",
                        transport="streamable_http",
                        desired="enabled",
                    )
                ],
                total_live=1,
                next_cursor=None,
            )

    engine = SimpleNamespace(
        graph_compute=SimpleNamespace(
            client=SimpleNamespace(server_registry=_Servers(), fleet_catalog=None)
        )
    )
    from graph_os.gateway import ports

    monkeypatch.setattr(
        ports, "gateway_application", lambda: SimpleNamespace(engine=lambda: engine)
    )
    monkeypatch.setattr(
        registry_api,
        "_require_catalog_authority",
        lambda *, require_discovery_binding: ("tenant-a", "caller-a", ()),
    )

    async def direct_catalog_call(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(registry_api, "_offload_catalog_call", direct_catalog_call)
    page = asyncio.run(
        registry_api._list_kind(
            _request(), kind="servers", model=registry_api.RegistryServer
        )
    )
    assert page.status == "ok"
    assert page.count == 1
    assert page.items[0].url == "https://svc.example"
    assert calls == [(50, None)]

    app = FastAPI()
    registry_api.register_registry_routes(app)
    assert {"/api/registry", "/api/registry/servers", "/api/registry/tools"} <= {
        route.path for route in app.routes
    }
    assert all(
        route.methods == {"GET"}
        for route in app.routes
        if route.path.startswith("/api/registry")
    )


def test_registry_without_verified_authority_is_forbidden(monkeypatch) -> None:
    def denied(*, require_discovery_binding: bool) -> None:
        raise PermissionError("no verified session")

    monkeypatch.setattr(registry_api, "_require_catalog_authority", denied)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            registry_api._list_kind(
                _request(), kind="servers", model=registry_api.RegistryServer
            )
        )
    assert error.value.status_code == 403


def test_registry_engine_composition_failure_is_explicitly_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        registry_api,
        "_require_catalog_authority",
        lambda *, require_discovery_binding: ("tenant-a", "caller-a", ()),
    )

    def unavailable_engine() -> None:
        raise RuntimeError("engine is not composed")

    monkeypatch.setattr(registry_api, "_get_catalog_engine", unavailable_engine)
    single = asyncio.run(
        registry_api._list_kind(
            _request(), kind="servers", model=registry_api.RegistryServer
        )
    )
    assert single.status_code == 503
    assert json.loads(single.body) == {
        "status": "unavailable",
        "reason": "catalog_unavailable",
    }

    multi_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/registry",
            "query_string": b"kinds=servers,tools",
            "headers": [],
        }
    )
    multi = asyncio.run(registry_api._list_multi_kind(multi_request))
    assert set(multi.kinds) == {"servers", "tools"}
    assert {result.status for result in multi.kinds.values()} == {"unavailable"}
    assert {result.reason for result in multi.kinds.values()} == {"catalog_unavailable"}


def test_registry_authority_comes_from_public_verified_session(monkeypatch) -> None:
    from agent_utilities.api import session

    actor = ActorContext(
        actor_id="caller-a",
        actor_type=ActorType.HUMAN,
        tenant_id="tenant-a",
        authenticated=True,
        roles=("kg:read",),
    )
    scopes: list[str] = []
    monkeypatch.setattr(
        session,
        "resolve_session",
        lambda *, required_scope: (
            scopes.append(required_scope)
            or SimpleNamespace(tenant="tenant-a", actor=actor)
        ),
    )
    monkeypatch.setattr(
        registry_api, "_resolve_current_discovery_grants", lambda _actor: ("digest-a",)
    )
    assert registry_api._require_catalog_authority(require_discovery_binding=True) == (
        "tenant-a",
        "caller-a",
        ("digest-a",),
    )
    assert registry_api._require_catalog_authority(require_discovery_binding=False) == (
        "tenant-a",
        "caller-a",
        (),
    )
    assert scopes == ["kg:read", "kg:read"]


def test_registry_toggle_projection_uses_one_engine_query() -> None:
    class _Engine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, list[str]]]] = []

        def query_cypher(
            self, query: str, params: dict[str, list[str]]
        ) -> list[dict[str, str]]:
            self.calls.append((query, params))
            return [
                {"id": "preference:toggle:mcp_tool:svc-a:tool-a", "value": "disabled"}
            ]

    engine = _Engine()
    result = registry_api._get_toggle_states_batch(
        engine, [("mcp_tool", "svc-a:tool-a"), ("mcp_server", "svc-a")]
    )
    assert result == {
        ("mcp_tool", "svc-a:tool-a"): False,
        ("mcp_server", "svc-a"): True,
    }
    assert len(engine.calls) == 1
