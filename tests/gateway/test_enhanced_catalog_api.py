"""W6 GraphOS-owned browser catalog contract."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.fleet.catalog_reader import (
    CatalogComponent,
    ComponentContent,
    ComponentPin,
    ComponentRecord,
    FleetCatalog,
    ReadContext,
    ReadReceipt,
    ServerRegistration,
)
from graph_os.fleet.catalog_reader import (
    CatalogServer as FleetServer,
)
from graph_os.gateway import enhanced_catalog_api as api

CONTEXT = ReadContext(
    tenant_id="tenant-a",
    principal_id="principal-a",
    agent_id="agent-a",
    audience="graph-os",
    policy_version="policy-a",
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _content(entry: ComponentRecord) -> ComponentContent:
    return ComponentContent(
        component_id=entry.component_id,
        entry_revision=entry.entry_revision,
        definition_digest=entry.definition_digest,
        content_digest=entry.content_digest,
        media_type="application/json",
        body=b"{}",
        receipt=ReadReceipt(
            context=CONTEXT,
            source="AgentComponent.Content",
            graph=CONTEXT.tenant_id,
        ),
    )


def _entry(
    component_id: str, kind: str, name: str, server: ComponentRecord | None
) -> ComponentRecord:
    return ComponentRecord(
        component_id=component_id,
        kind=kind,  # type: ignore[arg-type]
        server_name="source-a",
        upstream_name=name,
        summary=f"{name} description",
        tenant_id=CONTEXT.tenant_id,
        entry_revision=2,
        definition_digest=_digest(component_id + ":definition"),
        content_digest=_digest(component_id + ":content"),
        lifecycle="published",
        provenance_server=(
            None
            if server is None
            else ComponentPin(
                component_id=server.component_id,
                kind="mcp_server",
                definition_digest=server.definition_digest,
            )
        ),
    )


class FleetReader:
    def __init__(self, *, live: bool = True) -> None:
        server = _entry("component:server", "mcp_server", "source-a", None)
        tool = _entry("component:tool", "tool", "query", server)
        skill = _entry("component:skill", "skill", "triage", server)
        self.catalog = FleetCatalog(
            context=CONTEXT,
            servers=(
                FleetServer(
                    registration=(
                        ServerRegistration(
                            server_id="srv:source-a",
                            name="source-a",
                            url="https://source-a.example/mcp",
                            resources=(),
                            registered_at_ms=1,
                            last_heartbeat_ms=2,
                            lease_expires_at_ms=10_000,
                        )
                        if live
                        else None
                    ),
                    component=server,
                    content=_content(server),
                    provides=(
                        CatalogComponent(entry=tool, content=_content(tool)),
                        CatalogComponent(entry=skill, content=_content(skill)),
                    ),
                ),
            ),
        )

    async def read(self, *, kinds: Any) -> FleetCatalog:
        assert kinds == ("mcp_server", "tool", "skill")
        return self.catalog


class Workflows:
    async def list_current_workflows(self) -> tuple[api.WorkflowCatalogRecord, ...]:
        return (
            api.WorkflowCatalogRecord(
                workflow_id="workflow:triage",
                name="Triage workflow",
                description="Verified incident triage",
                status="active",
                revision=4,
                definition_digest=_digest("workflow:triage"),
            ),
        )


class Agents:
    async def list_authorized_agents(self) -> tuple[api.AgentCatalogRecord, ...]:
        return (
            api.AgentCatalogRecord(
                agent_id="agent:operator",
                name="Operator",
                description="Authorized operations agent",
                tools=("component:tool",),
            ),
        )


def _app(monkeypatch: Any) -> TestClient:
    authority = api.EnhancedCatalogAuthority(
        fleet=FleetReader(),  # type: ignore[arg-type]
        workflows=Workflows(),
        agents=Agents(),
    )
    monkeypatch.setattr(api, "_reader", authority.read)
    app = FastAPI()
    api.register_enhanced_catalog_routes(app)
    return TestClient(app)


def test_tools_contract_joins_exact_authorities_and_counts(monkeypatch: Any) -> None:
    response = _app(monkeypatch).get("/api/enhanced/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "epistemic_graph"
    assert body["counts"] == {
        "servers": 1,
        "tools": 1,
        "skills": 1,
        "workflows": 1,
    }
    assert body["servers"] == [
        {
            "server_id": "srv:source-a",
            "name": "source-a",
            "url": "https://source-a.example/mcp",
            "status": "available",
            "tool_count": 1,
            "error": None,
        }
    ]
    assert [item["kind"] for item in body["components"]] == [
        "skill",
        "tool",
        "workflow",
    ]
    workflow = body["components"][2]
    assert workflow["authority"] == "workflow_catalog"
    assert workflow["server_name"] is None
    assert workflow["content_digest"] is None
    assert all(
        item["authority"] == "agent_component" for item in body["components"][:2]
    )


def test_skills_and_capabilities_share_the_catalog_snapshot(monkeypatch: Any) -> None:
    client = _app(monkeypatch)

    skills = client.get("/api/enhanced/skills")
    capabilities = client.get("/api/enhanced/workflows/capabilities")

    assert skills.status_code == 200
    assert [item["id"] for item in skills.json()] == ["component:skill"]
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "agents": [
            {
                "id": "agent:operator",
                "name": "Operator",
                "kind": "agent",
                "system_prompt": None,
                "tools": ["component:tool"],
                "description": "Authorized operations agent",
            }
        ],
        "tools": [
            {
                "id": "component:tool",
                "name": "query",
                "kind": "tool",
                "system_prompt": None,
                "tools": None,
                "description": "query description",
            }
        ],
        "skills": [
            {
                "id": "component:skill",
                "name": "triage",
                "kind": "skill",
                "system_prompt": None,
                "tools": None,
                "description": "triage description",
            }
        ],
    }


def test_current_component_without_live_registration_is_explicitly_unavailable(
    monkeypatch: Any,
) -> None:
    authority = api.EnhancedCatalogAuthority(
        fleet=FleetReader(live=False),  # type: ignore[arg-type]
        workflows=Workflows(),
        agents=Agents(),
    )
    monkeypatch.setattr(api, "_reader", authority.read)
    app = FastAPI()
    api.register_enhanced_catalog_routes(app)

    response = TestClient(app).get("/api/enhanced/tools")

    assert response.status_code == 200
    assert response.json()["servers"] == [
        {
            "server_id": "srv:source-a",
            "name": "source-a",
            "url": "",
            "status": "unavailable",
            "tool_count": 1,
            "error": "live registration unavailable",
        }
    ]


def test_missing_or_failed_authority_never_fabricates_an_empty_catalog(
    monkeypatch: Any,
) -> None:
    app = FastAPI()
    api.register_enhanced_catalog_routes(app)
    client = TestClient(app)

    monkeypatch.setattr(api, "_reader", None)
    missing = client.get("/api/enhanced/tools")
    assert missing.status_code == 503
    assert missing.json() == {"detail": "catalog authority is not configured"}

    async def broken() -> Any:
        raise RuntimeError("private engine details")

    monkeypatch.setattr(api, "_reader", broken)
    failed = client.get("/api/enhanced/tools")
    assert failed.status_code == 503
    assert failed.json() == {"detail": "catalog authority unavailable"}


def test_route_module_has_no_webui_or_au_gateway_fallback() -> None:
    source = api.__file__
    assert source is not None
    text = __import__("pathlib").Path(source).read_text(encoding="utf-8")
    assert "agent_webui" not in text
    assert "agent_utilities.gateway" not in text
