"""arbora's MCP client surface for the harness: client toolsets and sessions.

CONCEPT:ARBORA-HOST.harness.host-bindings

agent-utilities carries no MCP client. When it needs a toolset bound to one
fleet endpoint, the toolsets a client configuration file declares, or one
``list_tools``/``call_tool`` session, it asks the host port
(``agent_utilities.host_ports``); arbora answers through these builders,
attaching its own outbound service credential and TLS policy.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

DEFAULT_MCP_TIMEOUT = 60.0


def _service_auth() -> Any:
    from arbora.mcp_server.backing.client_credentials import child_auth

    return child_auth(None)


def _http_client_factory(trust: Any, default_timeout: float) -> Any:
    """An httpx client factory closing over the resolved TLS policy and timeout."""
    from arbora.core.transport import create_async_http_client

    def factory(
        headers: dict[str, str] | None = None,
        timeout: Any | None = None,
        auth: Any | None = None,
        follow_redirects: bool = True,
        **_forward_compat: Any,
    ) -> Any:
        return create_async_http_client(
            headers=headers,
            auth=auth,
            timeout=_local_timeout(timeout, default_timeout),
            follow_redirects=follow_redirects,
            **trust.httpx_kwargs(),
        )

    return factory


def _local_timeout(value: Any, default_timeout: float) -> Any:
    """Rebuild any timeout-shaped value as this process's ``httpx.Timeout``.

    fastmcp's transport hands its own (vendored ``httpx2``) ``Timeout``; httpx
    only special-cases its own class, so a foreign instance is rebuilt from its
    ``connect``/``read``/``write``/``pool`` fields.
    """
    import httpx

    if value is None:
        return httpx.Timeout(default_timeout)
    if isinstance(value, httpx.Timeout | int | float | tuple):
        return httpx.Timeout(value)
    fields = ("connect", "read", "write", "pool")
    if all(hasattr(value, name) for name in fields):
        return httpx.Timeout(**{name: getattr(value, name) for name in fields})
    raise TypeError(f"unsupported MCP client timeout type: {type(value)!r}")


def build_url_toolset(
    url: str, toolset_id: str | None = None, timeout: float = DEFAULT_MCP_TIMEOUT
) -> Any:
    """One ``MCPToolset`` bound to ``url`` with arbora's outbound credential.

    A ``/sse`` URL selects the SSE transport; any other URL streamable HTTP.
    """
    from pydantic_ai.mcp import MCPToolset, SSETransport, StreamableHttpTransport

    from arbora.core.transport import resolve_configured_tls_profile
    from arbora.fleet.protocol_compat import force_legacy_protocol_mode
    from arbora.mcp_server.backing.httpx_boundary import coerce_httpx2_auth

    trust = resolve_configured_tls_profile("mcp")
    try:
        auth = _service_auth()
        if str(url).rstrip("/").lower().endswith("/sse"):
            transport: Any = SSETransport(
                url,
                auth=coerce_httpx2_auth(auth),
                httpx_client_factory=_http_client_factory(trust, timeout),
            )
        else:
            transport = StreamableHttpTransport(
                url,
                auth=auth,
                httpx_client_factory=_http_client_factory(trust, timeout),
            )
        toolset = (
            MCPToolset(transport, id=toolset_id)
            if toolset_id
            else MCPToolset(transport)
        )
        return force_legacy_protocol_mode(toolset)
    finally:
        trust.cleanup()


def load_config_toolsets(config_path: Path) -> list[Any]:
    """The toolsets a client configuration file (``mcpServers``) declares."""
    from pydantic_ai.mcp import load_mcp_toolsets

    from arbora.fleet.protocol_compat import force_legacy_protocol_mode

    return list(force_legacy_protocol_mode(load_mcp_toolsets(Path(config_path))))


class _HostToolClient:
    """A fastmcp client session that reports tool errors as ``HostToolError``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def list_tools(self) -> list[Any]:
        return list(await self._client.list_tools())

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        from agent_utilities.host_ports import HostToolError
        from fastmcp.exceptions import ToolError

        try:
            return await self._client.call_tool(name, arguments or {}, **kwargs)
        except ToolError as exc:
            raise HostToolError(str(exc)) from exc

    async def read_resource(self, uri: str) -> list[Any]:
        return list(await self._client.read_resource(uri))


@contextlib.asynccontextmanager
async def open_tool_client(
    target: Any, *, timeout: float | None = None
) -> AsyncIterator[_HostToolClient]:
    """Open one tool-client session on ``target``.

    ``target`` is a URL, an ``mcpServers`` mapping, or an in-process server.
    A bare HTTP(S) URL carries arbora's outbound service credential.
    """
    from fastmcp import Client

    auth = None
    if isinstance(target, str) and target.startswith(("http://", "https://")):
        from arbora.mcp_server.backing.httpx_boundary import coerce_httpx2_auth

        auth = coerce_httpx2_auth(_service_auth())
    async with Client(target, timeout=timeout, auth=auth) as client:
        yield _HostToolClient(client)


def remote_toolset_provider() -> Any:
    """The ``RemoteToolsetProvider`` arbora registers with the harness."""
    from agent_utilities.host_ports import RemoteToolsetProvider

    return RemoteToolsetProvider(
        build_url_toolset=build_url_toolset,
        load_config_toolsets=load_config_toolsets,
    )


__all__ = [
    "build_url_toolset",
    "load_config_toolsets",
    "open_tool_client",
    "remote_toolset_provider",
]
