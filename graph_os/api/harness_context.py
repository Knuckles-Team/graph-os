"""Verified MCP context endpoint export for harness RunToolsets (RF-029)."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from agent_connector_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from agent_utilities.layers.contracts import McpEndpoint

_REQUIRED_TOOLS = frozenset({"ask", "find"})
_REQUIRED_OPS = frozenset({"context.view", "query.uql"})
_MAX_REGISTRY_BYTES = 65_536


class ContextEndpointUnavailable(RuntimeError):
    """The configured endpoint did not prove caller-authorized EG context."""


@dataclass(frozen=True, slots=True)
class ContextCapabilityProof:
    endpoint_url: str
    registry_digest: str
    tools: frozenset[str]
    operations: frozenset[str]


def context_endpoint_payload(
    endpoint: McpEndpoint, proof: ContextCapabilityProof
) -> dict[str, object]:
    """Stable reference-only JSON projection for the served API operation."""

    if endpoint.url != proof.endpoint_url:
        raise ContextEndpointUnavailable("EG MCP proof does not match endpoint")
    return {
        "endpoint": endpoint.model_dump(mode="json"),
        "proof": {
            "endpoint_url": proof.endpoint_url,
            "registry_digest": proof.registry_digest,
            "tools": sorted(proof.tools),
            "operations": sorted(proof.operations),
        },
    }


BearerResolver = Callable[[str], Awaitable[str]]


def _endpoint_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ContextEndpointUnavailable("Invalid MCP public base URL") from exc
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
        or parsed.scheme not in {"http", "https"}
    ):
        raise ContextEndpointUnavailable("Invalid MCP public base URL")
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise ContextEndpointUnavailable("MCP context requires HTTPS or loopback")
    return urlunsplit((parsed.scheme, parsed.netloc, "/mcp", "", ""))


async def export_verified_eg_context_endpoint(
    *,
    public_base_url: str,
    bearer_ref: str,
    resolve_bearer: BearerResolver,
    timeout_s: float = 10.0,
) -> tuple[McpEndpoint, ContextCapabilityProof]:
    """Probe the existing served GraphOS MCP boundary under a real bearer.

    The caller resolves the reference from its secret authority. Only the
    reference is exported; the token is used for this bounded probe and never
    appears in the returned RunToolset descriptor.
    """

    try:
        reference = parse_secret_reference(bearer_ref)
    except SecretReferenceError as exc:
        raise ContextEndpointUnavailable("MCP bearer reference required") from exc
    if not 0 < timeout_s <= 30:
        raise ContextEndpointUnavailable("Invalid MCP probe timeout")
    url = _endpoint_url(public_base_url)
    try:
        token = await resolve_bearer(reference.render())
        if (
            not isinstance(token, str)
            or not token.strip()
            or len(token) > 4096
            or any(char in token for char in "\r\n\x00")
        ):
            raise ContextEndpointUnavailable("MCP bearer is unavailable")
        from fastmcp import Client

        async with asyncio.timeout(timeout_s):
            async with Client(url, auth=token, timeout=timeout_s) as client:
                tools = frozenset(tool.name for tool in await client.list_tools())
                if not _REQUIRED_TOOLS <= tools:
                    raise ContextEndpointUnavailable("EG MCP tools are unavailable")
                resources = await client.read_resource("graphos://registry")
    except ContextEndpointUnavailable:
        raise
    except Exception as exc:
        raise ContextEndpointUnavailable("EG MCP probe failed") from exc
    if len(resources) != 1 or not isinstance(getattr(resources[0], "text", None), str):
        raise ContextEndpointUnavailable("EG MCP registry is unavailable")
    raw = resources[0].text
    if len(raw.encode()) > _MAX_REGISTRY_BYTES:
        raise ContextEndpointUnavailable("EG MCP registry is oversized")
    try:
        index = json.loads(raw)
    except ValueError as exc:
        raise ContextEndpointUnavailable("EG MCP registry is invalid") from exc
    if not isinstance(index, dict):
        raise ContextEndpointUnavailable("EG MCP registry is invalid")
    digest = index.get("registry_digest")
    operations = index.get("ops")
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(operations, list)
        or any(not isinstance(op, str) for op in operations)
    ):
        raise ContextEndpointUnavailable("EG MCP registry proof is invalid")
    authorized = frozenset(operations)
    if not _REQUIRED_OPS <= authorized:
        raise ContextEndpointUnavailable("EG context authority is unavailable")
    return (
        McpEndpoint(
            name="epistemic-graph-context",
            url=url,
            transport="http",
            bearer_ref=reference.render(),
        ),
        ContextCapabilityProof(url, digest, tools, authorized),
    )


async def configured_eg_context_endpoint(
    *, bearer_ref: str, resolve_bearer: BearerResolver
) -> tuple[McpEndpoint, ContextCapabilityProof]:
    """Use the same public base URL configured for the served MCP host."""

    from agent_connector_sdk.config import setting

    base = setting("MCP_PUBLIC_BASE_URL", "")
    return await export_verified_eg_context_endpoint(
        public_base_url=str(base or ""),
        bearer_ref=bearer_ref,
        resolve_bearer=resolve_bearer,
    )
