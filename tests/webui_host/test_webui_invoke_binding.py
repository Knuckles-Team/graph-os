"""WebUI host delegates only under the browser's verified request authority."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from graph_os.api.invoke import OpError, OpResult
from graph_os.api.registry import Surface
from graph_os.webui_host.webui_co_service import (
    _invoke_webui_operation,
    compose_web_application,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/enhanced/atlas/sources",
            "headers": [(b"x-request-id", b"webui-1")],
        }
    )


def _session() -> Any:
    actor = SimpleNamespace(
        actor_id="user:verified",
        actor_type="human",
        authenticated=True,
        ensure_credential_current=lambda: None,
    )
    return SimpleNamespace(
        actor=actor,
        tenant="tenant:verified",
        scopes=frozenset({"kg:read"}),
        policy_version="policy:1",
        ensure_authority_current=lambda: None,
        engine_verified_context=lambda: {
            "principal": actor.actor_id,
            "tenant": "tenant:verified",
            "scopes": ["kg:read"],
            "policy_version": "policy:1",
        },
    )


def test_composer_installs_graphos_invoke_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "graph_os.gateway.graph_api.register_graph_routes", lambda _app: None
    )
    monkeypatch.setattr(
        "graph_os.webui_host.a2a_routes.register_a2a_routes", lambda _app: None
    )
    app = FastAPI()
    compose_web_application(app)
    assert app.state.graphos_invoke_op is _invoke_webui_operation


@pytest.mark.asyncio
async def test_invoke_uses_browser_principal_and_shared_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.core.session.resolve_session", lambda: session
    )
    services = SimpleNamespace(registry=SimpleNamespace(digest="digest:1"))
    projection = SimpleNamespace(services=services)
    monkeypatch.setattr(
        "graph_os.mcp_server.runtime.served_api",
        lambda: (projection, object()),
        raising=False,
    )
    observed: dict[str, Any] = {}

    async def invoke(
        op_id: str, params: Any, caller: Any, surface: Surface, *, services: Any
    ) -> OpResult:
        observed.update(
            op_id=op_id,
            params=params,
            principal=caller.principal,
            tenant=caller.tenant,
            scopes=caller.effective_scopes,
            request_id=caller.request_id,
            session=caller.session,
            surface=surface,
            services=services,
        )
        return OpResult(value={"providers": []})

    monkeypatch.setattr("graph_os.api.invoke.invoke", invoke)
    result = await _invoke_webui_operation(_request(), "atlas.sources.list", {})
    assert result == {"providers": []}
    assert observed == {
        "op_id": "atlas.sources.list",
        "params": {},
        "principal": "user:verified",
        "tenant": "tenant:verified",
        "scopes": frozenset({"kg:read"}),
        "request_id": "webui-1",
        "session": session,
        "surface": Surface.HTTP,
        "services": services,
    }


@pytest.mark.asyncio
async def test_invoke_refuses_missing_browser_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> Any:
        raise PermissionError("synthetic missing ambient session")

    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.core.session.resolve_session", missing
    )
    with pytest.raises(HTTPException) as exc:
        await _invoke_webui_operation(_request(), "atlas.sources.list", {})
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_invoke_refuses_unbound_serving_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.core.session.resolve_session", _session
    )

    def unbound() -> Any:
        raise RuntimeError("synthetic missing policy and registry")

    monkeypatch.setattr(
        "graph_os.mcp_server.runtime.served_api", unbound, raising=False
    )
    with pytest.raises(HTTPException) as exc:
        await _invoke_webui_operation(_request(), "atlas.sources.list", {})
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_invoke_preserves_governed_refusal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.core.session.resolve_session", _session
    )
    projection = SimpleNamespace(
        services=SimpleNamespace(registry=SimpleNamespace(digest="digest:1"))
    )
    monkeypatch.setattr(
        "graph_os.mcp_server.runtime.served_api",
        lambda: (projection, object()),
        raising=False,
    )

    async def denied(*_args: Any, **_kwargs: Any) -> OpError:
        return OpError("POLICY_DENIED")

    monkeypatch.setattr("graph_os.api.invoke.invoke", denied)
    with pytest.raises(HTTPException) as exc:
        await _invoke_webui_operation(_request(), "atlas.sources.list", {})
    assert exc.value.status_code == 403
