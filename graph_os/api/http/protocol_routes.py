"""Native protocol paths exempt from the operation-registry surface gate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolRoute:
    path: str
    owner: str
    reason: str


PROTOCOL_ROUTES = (
    ProtocolRoute("/a2a", "graph_os.a2a", "A2A JSON-RPC transport"),
    ProtocolRoute(
        "/.well-known/agent-card.json", "graph_os.a2a", "Agent Card discovery"
    ),
    ProtocolRoute(
        "/.well-known/ai-catalog.json", "graph_os.gateway", "AI catalog discovery"
    ),
    ProtocolRoute(
        "/.well-known/jwks.json", "graph_os.identity", "Public key discovery"
    ),
    ProtocolRoute(
        "/.well-known/openid-configuration", "graph_os.identity", "OIDC discovery"
    ),
    ProtocolRoute(
        "/auth/{path:path}", "graph_os.identity", "Browser auth state machine"
    ),
    ProtocolRoute("/scim/v2/{path:path}", "graph_os.identity", "SCIM protocol"),
    ProtocolRoute("/oauth/{path:path}", "graph_os.identity", "OAuth protocol"),
    ProtocolRoute("/fleet/events", "graph_os.fleet", "Signed child webhook ingress"),
    ProtocolRoute("/health", "graph_os.deployment", "Liveness probe"),
    ProtocolRoute("/health/ready", "graph_os.deployment", "Readiness probe"),
    ProtocolRoute("/metrics", "graph_os.gateway", "Authenticated metrics"),
    ProtocolRoute("/mcp", "graph_os.mcp_server", "MCP transport"),
    ProtocolRoute("/search", "graph_os.gateway", "Agentic RAG transport"),
)
