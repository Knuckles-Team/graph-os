"""RF-029 operation exports only a verified reference to service callers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_utilities.layers.contracts import McpEndpoint

from graph_os.api.harness_context import ContextCapabilityProof
from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops.harness import context_endpoint_handler, specs
from graph_os.api.registry import PrincipalRule, Surface, Verb


def _proof() -> ContextCapabilityProof:
    return ContextCapabilityProof(
        endpoint_url="https://graph.example/mcp",
        registry_digest="a" * 64,
        tools=frozenset({"ask", "find"}),
        operations=frozenset({"context.view", "query.uql"}),
    )


def _endpoint() -> McpEndpoint:
    return McpEndpoint(
        name="epistemic-graph-context",
        url="https://graph.example/mcp",
        transport="http",
        bearer_ref="env://GRAPHOS_CONTEXT_TOKEN",
    )


@pytest.mark.asyncio
async def test_context_endpoint_is_exact_service_operation_with_reference_only() -> (
    None
):
    op = specs()[0]
    assert op.id == "harness.context_endpoint"
    assert op.verb is Verb.MANAGE
    assert op.principals is PrincipalRule.SERVICE_ONLY
    assert op.scopes == {"mcp:discover", "mcp:delegate"}
    assert op.surfaces == {Surface.HTTP, Surface.MCP}

    async def export():
        return _endpoint(), _proof()

    context = SimpleNamespace(services={"context_endpoint_export": export})
    result = await context_endpoint_handler(context, {}, op)
    assert result["endpoint"]["bearer_ref"] == "env://GRAPHOS_CONTEXT_TOKEN"
    assert result["proof"]["endpoint_url"] == result["endpoint"]["url"]
    assert result["proof"]["operations"] == ["context.view", "query.uql"]
    assert "token" not in result["endpoint"]


@pytest.mark.asyncio
async def test_context_endpoint_fails_closed_without_exporter_or_matching_proof() -> (
    None
):
    op = specs()[0]
    with pytest.raises(OperationRefused) as missing:
        await context_endpoint_handler(SimpleNamespace(services={}), {}, op)
    assert missing.value.code == "UNAVAILABLE"

    async def mismatched_export():
        proof = _proof()
        return _endpoint().model_copy(
            update={"url": "https://other.example/mcp"}
        ), proof

    with pytest.raises(OperationRefused) as mismatched:
        await context_endpoint_handler(
            SimpleNamespace(services={"context_endpoint_export": mismatched_export}),
            {},
            op,
        )
    assert mismatched.value.code == "UNAVAILABLE"
