"""RF-029: a harness receives EG context only after live MCP proof."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from graph_os.api.harness_context import (
    ContextEndpointUnavailable,
    export_verified_eg_context_endpoint,
)


class _Client:
    tools = ("ask", "find")
    ops = ("context.view", "query.uql")
    seen: list[tuple[str, str]] = []

    def __init__(self, url: str, *, auth: str, timeout: float) -> None:
        assert timeout == 10
        self.seen.append((url, auth))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def list_tools(self):
        return [SimpleNamespace(name=name) for name in self.tools]

    async def read_resource(self, uri: str):
        assert uri == "graphos://registry"
        return [
            SimpleNamespace(
                text=json.dumps(
                    {"registry_digest": "a" * 64, "ops": list(self.ops)}
                )
            )
        ]


async def _secret(reference: str) -> str:
    assert reference == "env://HARNESS_EG_BEARER"
    return "private-bearer"


@pytest.mark.asyncio
async def test_export_requires_live_authorized_mcp_and_keeps_token_out(
    monkeypatch,
) -> None:
    import fastmcp

    _Client.seen.clear()
    monkeypatch.setattr(fastmcp, "Client", _Client)
    endpoint, proof = await export_verified_eg_context_endpoint(
        public_base_url="https://graph.example.test/",
        bearer_ref="env://HARNESS_EG_BEARER",
        resolve_bearer=_secret,
    )
    assert endpoint.name == "epistemic-graph-context"
    assert endpoint.url == "https://graph.example.test/mcp"
    assert endpoint.bearer_ref == "env://HARNESS_EG_BEARER"
    assert proof.registry_digest == "a" * 64
    assert proof.operations == frozenset(_Client.ops)
    assert _Client.seen == [(endpoint.url, "private-bearer")]
    assert "private-bearer" not in repr(endpoint) + repr(proof)


@pytest.mark.asyncio
async def test_export_refuses_missing_capability_and_unsafe_config(monkeypatch) -> None:
    import fastmcp

    class NoContext(_Client):
        ops = ("query.uql",)

    monkeypatch.setattr(fastmcp, "Client", NoContext)
    with pytest.raises(ContextEndpointUnavailable, match="context authority"):
        await export_verified_eg_context_endpoint(
            public_base_url="https://graph.example.test",
            bearer_ref="env://HARNESS_EG_BEARER",
            resolve_bearer=_secret,
        )
    for base in (
        "http://graph.example.test",
        "https://user:pass@graph.example.test",
        "https://graph.example.test/?token=x",
        "https://graph.example.test/path",
    ):
        with pytest.raises(ContextEndpointUnavailable):
            await export_verified_eg_context_endpoint(
                public_base_url=base,
                bearer_ref="env://HARNESS_EG_BEARER",
                resolve_bearer=_secret,
            )


@pytest.mark.asyncio
async def test_export_refuses_missing_bearer_and_tool_list(monkeypatch) -> None:
    import fastmcp

    class NoAsk(_Client):
        tools = ("find",)

    monkeypatch.setattr(fastmcp, "Client", NoAsk)
    with pytest.raises(ContextEndpointUnavailable, match="tools"):
        await export_verified_eg_context_endpoint(
            public_base_url="http://127.0.0.1:8000",
            bearer_ref="env://HARNESS_EG_BEARER",
            resolve_bearer=_secret,
        )
    with pytest.raises(ContextEndpointUnavailable, match="reference"):
        await export_verified_eg_context_endpoint(
            public_base_url="https://graph.example.test",
            bearer_ref="",
            resolve_bearer=_secret,
        )


@pytest.mark.asyncio
async def test_export_refuses_unreachable_mcp_without_exporting_token(monkeypatch) -> None:
    import fastmcp

    class Unreachable(_Client):
        async def __aenter__(self):
            raise ConnectionError("private-bearer")

    monkeypatch.setattr(fastmcp, "Client", Unreachable)
    with pytest.raises(ContextEndpointUnavailable, match="probe failed") as caught:
        await export_verified_eg_context_endpoint(
            public_base_url="https://graph.example.test",
            bearer_ref="env://HARNESS_EG_BEARER",
            resolve_bearer=_secret,
        )
    assert "private-bearer" not in str(caught.value)
