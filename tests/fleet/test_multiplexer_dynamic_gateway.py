"""Tests for the dynamic tool gateway (CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog).

Covers the lazy-mount refactor, KG-backed discovery + (server, tool) mapping,
the load/unload resolution logic, and the FastMCP meta-tool wiring. Children and
the knowledge-graph are mocked so these run with no processes or live engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import mcp.types
import pytest
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool

from graph_os.fleet.multiplexer import (
    _LOCAL_SESSION_META_KEY,
    _fleet_required_capabilities,
    _make_forwarder,
    _register_forwarder,
    _require_fleet_capability,
    _session_key,
    _tool_is_verbose,
    get_server_prefix,
)
from tests.fleet.catalog_fixture import (
    bind_governed_forwarder_fixture,
    multiplexer_from_fixture,
)

CNT = "container-manager-mcp"
CNT_TOOL = "cm_container_operations"
# container-manager-mcp auto-derives prefix "cm"; clean_tool_name then strips the
# redundant leading "cm_" from the tool name → "cm__container_operations".
CNT_PREFIXED = "cm__container_operations"
CNT_IMAGE_PREFIXED = "cm__image_operations"
CNT_INFO_PREFIXED = "cm__info_operations"


def _write_config(tmp_path: Path, servers: dict[str, Any]) -> Path:
    path = tmp_path / "mcp_config.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_forwarder_preserves_child_tool_error_as_outer_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mux = multiplexer_from_fixture(tmp_path / "mcp_config.json")
    fixture = await bind_governed_forwarder_fixture(
        mux,
        mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="private child detail")],
            is_error=True,
        ),
    )

    with pytest.raises(ToolError, match="CHILD_REFUSED") as error:
        await _make_forwarder(mux, "synthetic__tool")()
    assert "private child detail" not in str(error.value)
    assert fixture.dispatches == ["fleet.call"]


@pytest.mark.spec("GRAPHOS-FLEET-R029", "GRAPHOS-DEPLOY-R004")
@pytest.mark.asyncio
async def test_remove_host_forwarder_converges_only_after_verified_absence(
    tmp_path, caplog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An idempotent reload is logged safely; a live forwarder is never hidden."""
    from fastmcp import FastMCP

    mux = multiplexer_from_fixture(_write_config(tmp_path, {}))
    host = FastMCP("forwarder-cleanup-host")
    mux._host_mcp = host
    absent_name = "synthetic__already_absent"
    mux._exposed.add(absent_name)

    with caplog.at_level(logging.INFO, logger="mcp_multiplexer"):
        mux._remove_host_forwarder(absent_name)

    assert absent_name not in mux._exposed
    assert "already absent during cleanup" in caplog.text
    assert absent_name not in caplog.text
    assert "tool_ref=<redacted:" in caplog.text

    live_tool = _schema_tool("synthetic__still_registered", "legacy")
    _register_forwarder(host, mux, live_tool)
    monkeypatch.setattr(
        host._local_provider,
        "remove_tool",
        lambda _name: (_ for _ in ()).throw(KeyError("provider mismatch")),
    )
    with pytest.raises(RuntimeError, match="remains registered"):
        mux._remove_host_forwarder(live_tool.name)
    assert live_tool.name in mux._exposed
    assert await host.get_tool(live_tool.name) is not None

    base_tool = Tool(name="synthetic__live_base_tool", parameters={})
    host._local_provider._components[base_tool.key] = base_tool
    mux._exposed.add(base_tool.name)
    with pytest.raises(RuntimeError, match="remains registered"):
        mux._remove_host_forwarder(base_tool.name)
    assert base_tool.name in mux._exposed
    assert host._local_provider._components[base_tool.key] is base_tool
    await mux.aclose()


def _fake_tool(
    name: str,
    description: str = "",
    schema: dict | None = None,
    tags: list[str] | None = None,
    meta: dict | None = None,
):
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.input_schema = schema if schema is not None else {}
    tool.annotations = None
    # FastMCP propagates tags via _meta; the multiplexer reads tool.meta.
    # An explicit `meta` (e.g. an MCP Apps `{"ui": {"resourceUri": ...}}`
    # tool-descriptor declaration, BUG-071) wins over the tags-derived default.
    if meta is not None:
        tool.meta = meta
    else:
        tool.meta = {"fastmcp": {"tags": list(tags)}} if tags is not None else None
    return tool


class _SchemaGenerationSession:
    """Small child-session fake for recovery/catalog-refresh regressions."""

    def __init__(
        self,
        tools: list[mcp.types.Tool],
        *,
        fail_calls: bool = False,
        tag: str,
    ) -> None:
        self.tools = tools
        self.fail_calls = fail_calls
        self.tag = tag
        self.listed = 0
        self.calls: list[str] = []

    async def list_tools(self) -> SimpleNamespace:
        self.listed += 1
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name: str, arguments: dict) -> mcp.types.CallToolResult:
        self.calls.append(name)
        if self.fail_calls:
            raise ConnectionResetError("synthetic child transport reset")
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=f"{self.tag}:{name}")]
        )


def _schema_tool(name: str, property_name: str) -> mcp.types.Tool:
    return mcp.types.Tool(
        name=name,
        description=f"{property_name} schema",
        input_schema={
            "type": "object",
            "properties": {property_name: {"type": "string"}},
        },
    )


async def _reconnect_mounted(mux, server_name: str, generations: list):
    """Install ``generations`` as a queue of canned sessions behind
    ``_open_one_session``, bind a fresh FastMCP host, and mount the server --
    the shared reconnect scaffold for the schema-refresh regression tests
    below."""
    from fastmcp import FastMCP

    async def fake_open_one_session(*_args):
        return generations.pop(0)

    mux._open_one_session = AsyncMock(side_effect=fake_open_one_session)
    host = FastMCP("schema-refresh-host")
    mux._host_mcp = host
    mounted = await mux.mount_child(server_name)
    return host, mounted


async def _list_session_tool_names(client: Any, session_id: str) -> set[str]:
    """List tool names visible to ``client`` under ``session_id`` -- shared
    by the session-visibility dynamic-gateway tests below."""
    from mcp.types import PaginatedRequestParams, RequestParamsMeta

    # ``RequestParamsMeta`` is declared ``extra_items=Any`` (an open map per
    # the MCP spec); mypy does not yet implement PEP 728's ``extra_items``,
    # so the reserved session key is cast to the TypedDict's own declared
    # type rather than widened to ``Any``.
    meta = cast(RequestParamsMeta, {_LOCAL_SESSION_META_KEY: session_id})
    result = await client.session.list_tools(params=PaginatedRequestParams(_meta=meta))
    return {t.name for t in result.tools}


async def _wait_for_condition(predicate, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


def _mux_with_children(tmp_path, tool_map: dict[str, list[tuple[str, str]]]):
    """Build a mux whose ``_start_child`` yields the given ``server -> [(tool,
    desc)]`` map instead of spawning real processes."""
    servers: dict[str, Any] = {
        name: {"command": "python", "args": ["-m", name]} for name in tool_map
    }
    servers["mcp-multiplexer"] = {"command": "self"}  # must be excluded
    servers["off"] = {"command": "python", "disabled": True}  # must be excluded
    mux = multiplexer_from_fixture(_write_config(tmp_path, servers))

    async def fake_start_child(server_name, cfg):
        tools = [_fake_tool(n, d) for n, d in tool_map.get(server_name, [])]
        session = AsyncMock()
        return server_name, session, tools, cfg

    mux._start_child = AsyncMock(side_effect=fake_start_child)  # type: ignore[method-assign]
    return mux


# --------------------------------------------------------------------------- #
# Catalog + lazy mount
# --------------------------------------------------------------------------- #


@pytest.mark.spec("GRAPHOS-DEPLOY-R004")
def test_load_catalog_excludes_self_and_disabled(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    catalog = mux.load_catalog()
    assert CNT in catalog
    assert "mcp-multiplexer" not in catalog
    assert "off" not in catalog
    # idempotent / cached
    assert mux.load_catalog() is catalog


def test_load_catalog_admits_explicit_remote_langfuse_entry_via_direct_key_pair(
    tmp_path, monkeypatch
):
    """Regression test for the graph-os fleet-catalog gap.

    An explicit remote (streamable-http) ``langfuse-mcp`` entry ships with no
    ``env`` block, so credential resolution falls through to the parent
    process environment. That resolution must accept the direct
    ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` pair -- the same names
    ``langfuse_agent.auth.get_client`` reads for the standalone agent/MCP
    server -- not only the ``LANGFUSE_PUBLIC_KEY_REF`` / ``LANGFUSE_SECRET_KEY_REF``
    secret-reference form.
    """
    config_path = _write_config(
        tmp_path,
        {
            "langfuse-mcp": {
                "transport": "streamable-http",
                "url": "http://langfuse-mcp.example.test/mcp",
                "timeout": 120,
                "call_timeout": 600,
                "disabled": False,
            }
        },
    )
    for name in ("LANGFUSE_PUBLIC_KEY_REF", "LANGFUSE_SECRET_KEY_REF"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-synthetic")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-synthetic")

    catalog = multiplexer_from_fixture(config_path).load_catalog()

    assert "langfuse-mcp" in catalog
    assert catalog["langfuse-mcp"]["transport"] == "streamable-http"
    assert catalog["langfuse-mcp"]["env"]["LANGFUSE_PUBLIC_KEY"] == "pk-lf-synthetic"
    assert catalog["langfuse-mcp"]["env"]["LANGFUSE_SECRET_KEY"] == "sk-lf-synthetic"


def test_load_catalog_still_rejects_when_neither_ref_nor_direct_key_present(
    tmp_path, monkeypatch, caplog
):
    config_path = _write_config(
        tmp_path,
        {
            "langfuse-mcp": {
                "transport": "streamable-http",
                "url": "http://langfuse-mcp.example.test/mcp",
                "disabled": False,
            }
        },
    )
    for name in (
        "LANGFUSE_PUBLIC_KEY_REF",
        "LANGFUSE_SECRET_KEY_REF",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.test")

    catalog = multiplexer_from_fixture(config_path).load_catalog()

    assert "langfuse-mcp" not in catalog
    assert "langfuse_credentials_missing" in caplog.text


@pytest.mark.spec("GRAPHOS-FLEET-R021")
async def test_mount_child_lazy_and_idempotent(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})

    tools = await mux.mount_child(CNT)
    assert [t.name for t in tools] == [CNT_PREFIXED]
    assert CNT in mux.children
    assert mux.tool_to_server[CNT_PREFIXED] == (CNT, CNT_TOOL)
    assert CNT_PREFIXED in {t.name for t in mux.aggregated_tools}
    assert mux._start_child.await_count == 1

    # Second mount must not re-spawn the child.
    again = await mux.mount_child(CNT)
    assert [t.name for t in again] == [CNT_PREFIXED]
    assert mux._start_child.await_count == 1


@pytest.mark.usefixtures("stdio_fleet_authority")
async def test_recovered_child_replaces_exposed_schema_without_catalog_polling(
    tmp_path,
):
    """A reconnect must refresh one changed schema from its handshake only.

    This is the deployed service-reload path: the first child session dies,
    its replacement advertises a changed ``tools/list`` schema, and the same
    forwarded tool remains client-visible with the fresh schema.  A further
    equivalent recycle proves ordinary calls and no-op generations do not
    cause provider introspection or cache churn.
    """
    server_name = "schema-mcp"
    config_path = _write_config(
        tmp_path,
        {server_name: {"command": "schema-child", "args": []}},
    )
    legacy = _SchemaGenerationSession(
        [_schema_tool("query", "legacy")], fail_calls=True, tag="legacy"
    )
    refreshed = _SchemaGenerationSession(
        [_schema_tool("query", "fresh")], tag="recovered"
    )
    equivalent = _SchemaGenerationSession(
        [_schema_tool("query", "fresh")], tag="equivalent"
    )
    generations = [legacy, refreshed, equivalent]
    mux = multiplexer_from_fixture(config_path)

    host, mounted = await _reconnect_mounted(mux, server_name, generations)
    assert len(mounted) == 1
    prefixed_name = mounted[0].name
    _register_forwarder(host, mux, mounted[0])
    assert (await host.get_tool(prefixed_name)).parameters["properties"] == {
        "legacy": {"type": "string"}
    }

    # A changed generation must invalidate derived discovery/embedding state,
    # but it must not add a second tools/list to the ordinary call path.
    mux._probe_cache[server_name] = {"tools": ["legacy"]}
    mux._tool_embeddings[f"{server_name}::query"] = [0.1, 0.2]
    runtime = mux.children[server_name]
    runtime.restart_backoff_base = 0.005
    runtime.restart_backoff_cap = 0.005
    result = await mux.call_proxied_tool(prefixed_name, {})
    assert result.content[0].text == "recovered:query"
    assert mux.sessions[server_name] is refreshed
    assert mux.tool_object(prefixed_name).input_schema["properties"] == {
        "fresh": {"type": "string"}
    }
    assert (await host.get_tool(prefixed_name)).parameters["properties"] == {
        "fresh": {"type": "string"}
    }
    assert server_name not in mux._probe_cache
    assert f"{server_name}::query" not in mux._tool_embeddings
    assert mux.status_snapshot()["children"][server_name]["catalog_revision"] == 2
    assert legacy.listed == 1
    assert refreshed.listed == 1

    # An ordinary subsequent tool call reuses the recovered connection — no
    # per-call list_tools probe is permitted on the hot delegation path.
    again = await mux.call_proxied_tool(prefixed_name, {})
    assert again.content[0].text == "recovered:query"
    assert refreshed.listed == 1

    # Cache state survives an equivalent planned recycle.  The child already
    # paid for one tools/list in that generation, and the digest proves it is
    # the same schema without touching host registrations or probe caches.
    mux._probe_cache[server_name] = {"tools": ["fresh"]}
    mux._tool_embeddings[f"{server_name}::query"] = [0.3, 0.4]
    runtime.request_recycle()
    await _wait_for_condition(lambda: runtime.state == "up" and equivalent.listed == 1)
    assert mux.sessions[server_name] is equivalent
    assert mux.status_snapshot()["children"][server_name]["catalog_revision"] == 2
    assert mux._probe_cache[server_name] == {"tools": ["fresh"]}
    assert mux._tool_embeddings[f"{server_name}::query"] == [0.3, 0.4]
    assert (await host.get_tool(prefixed_name)).parameters["properties"] == {
        "fresh": {"type": "string"}
    }
    await mux.aclose()


@pytest.mark.spec("GRAPHOS-FLEET-R021")
@pytest.mark.usefixtures("stdio_fleet_authority")
async def test_schema_refresh_failure_fails_closed_without_stranding_transport(
    tmp_path, monkeypatch
):
    """A persistent host add failure keeps the old live FastMCP tool intact."""
    server_name = "schema-mcp"
    mux = multiplexer_from_fixture(
        _write_config(
            tmp_path,
            {server_name: {"command": "schema-child", "args": []}},
        )
    )
    stale = _SchemaGenerationSession(
        [_schema_tool("query", "legacy")], fail_calls=True, tag="legacy"
    )
    recovered = _SchemaGenerationSession(
        [_schema_tool("query", "fresh")], tag="recovered"
    )
    generations = [stale, recovered]

    host, mounted = await _reconnect_mounted(mux, server_name, generations)
    prefixed_name = mounted[0].name
    _register_forwarder(host, mux, mounted[0])
    assert (await host.get_tool(prefixed_name)).parameters["properties"] == {
        "legacy": {"type": "string"}
    }

    def persistent_add_failure(_tool):
        raise RuntimeError("synthetic persistent FastMCP add_tool failure")

    # Fail every replacement registration after the original real FastMCP
    # tool has been published. The recovery must neither remove that actual
    # host component nor change the multiplexer routing/catalog state.
    monkeypatch.setattr(host, "add_tool", persistent_add_failure)
    runtime = mux.children[server_name]
    runtime.restart_backoff_base = 0.005
    runtime.restart_backoff_cap = 0.005

    result = await mux.call_proxied_tool(prefixed_name, {})
    assert result.is_error is True
    assert result.content[0].text == "schema_refresh_failed"
    assert runtime.state == "up"
    assert mux.sessions[server_name] is recovered
    assert (
        mux.status_snapshot()["children"][server_name]["catalog_refresh_error"]
        == "schema_refresh_failed"
    )
    assert (await host.get_tool(prefixed_name)).parameters["properties"] == {
        "legacy": {"type": "string"}
    }
    assert mux.tool_object(prefixed_name).input_schema["properties"] == {
        "legacy": {"type": "string"}
    }
    assert mux.tool_to_server[prefixed_name] == (server_name, "query")
    assert prefixed_name in mux._exposed
    await mux.aclose()


@pytest.mark.spec("GRAPHOS-FLEET-R021", "GRAPHOS-FLEET-R029")
@pytest.mark.usefixtures("stdio_fleet_authority")
async def test_schema_refresh_rolls_back_partial_host_registration_atomically(
    tmp_path, monkeypatch
):
    """A later persistent add failure cannot strand an earlier fresh schema.

    FastMCP 4.0.0b1 replaces a duplicate directly in its local provider.  That
    means a two-tool reconnect can accept the first replacement and reject the
    second one.  The multiplexer must restore the exact pre-refresh SDK
    component registry without calling the now-failing ``add_tool`` path.
    """
    server_name = "schema-mcp"
    mux = multiplexer_from_fixture(
        _write_config(
            tmp_path,
            {server_name: {"command": "schema-child", "args": []}},
        )
    )
    stale = _SchemaGenerationSession(
        [
            _schema_tool("query", "legacy_query"),
            _schema_tool("search", "legacy_search"),
        ],
        fail_calls=True,
        tag="legacy",
    )
    recovered = _SchemaGenerationSession(
        [
            _schema_tool("query", "fresh_query"),
            _schema_tool("search", "fresh_search"),
        ],
        tag="recovered",
    )
    generations = [stale, recovered]

    host, mounted = await _reconnect_mounted(mux, server_name, generations)
    prefixed_by_original = {
        original: prefixed
        for prefixed, (_server, original) in mux.tool_to_server.items()
    }
    for tool in mounted:
        _register_forwarder(host, mux, tool)

    before_components = dict(host._local_provider._components)
    original_add_tool = host.add_tool
    add_calls = 0

    def accept_one_then_fail_forever(tool):
        nonlocal add_calls
        add_calls += 1
        if add_calls == 1:
            return original_add_tool(tool)
        raise RuntimeError("synthetic persistent FastMCP add_tool failure")

    # This reproduces FastMCP's actual duplicate-replace behavior for the
    # first forwarded component, followed by a host registration path that is
    # unavailable for every subsequent attempt (including a naive rollback).
    monkeypatch.setattr(host, "add_tool", accept_one_then_fail_forever)
    runtime = mux.children[server_name]
    runtime.restart_backoff_base = 0.005
    runtime.restart_backoff_cap = 0.005

    result = await mux.call_proxied_tool(prefixed_by_original["query"], {})

    assert result.is_error is True
    assert result.content[0].text == "schema_refresh_failed"
    # Two attempted replacements only: the rollback is a single registry swap,
    # not a third doomed add_tool call.
    assert add_calls == 2
    assert set(host._local_provider._components) == set(before_components)
    for key, component in before_components.items():
        assert host._local_provider._components[key] is component
    for original, property_name in {
        "query": "legacy_query",
        "search": "legacy_search",
    }.items():
        prefixed = prefixed_by_original[original]
        assert (await host.get_tool(prefixed)).parameters["properties"] == {
            property_name: {"type": "string"}
        }
        assert mux.tool_object(prefixed).input_schema["properties"] == {
            property_name: {"type": "string"}
        }
        assert mux.tool_to_server[prefixed] == (server_name, original)
        assert prefixed in mux._exposed
    await mux.aclose()


_SCHEMA_SERVER = "schema-mcp"
_SCHEMA_SESSION = "connected-client"


async def _mount_schema_child(tmp_path):
    """Mount one schema-generating child, register its forwarder on a fresh
    FastMCP host, and mark it loaded for ``_SCHEMA_SESSION`` -- the shared
    setup for the ``_refresh_child_tools`` recovery/removal notification
    tests below. Returns ``(mux, host, prefixed_name, runtime)``."""
    from fastmcp import FastMCP

    mux = multiplexer_from_fixture(
        _write_config(
            tmp_path,
            {_SCHEMA_SERVER: {"command": "schema-child", "args": []}},
        )
    )
    original = _SchemaGenerationSession([_schema_tool("query", "legacy")], tag="legacy")

    async def fake_open_one_session(*_args):
        return original

    mux._open_one_session = AsyncMock(side_effect=fake_open_one_session)
    host = FastMCP("schema-refresh-host")
    mux._host_mcp = host
    mounted = await mux.mount_child(_SCHEMA_SERVER)
    prefixed_name = mounted[0].name
    _register_forwarder(host, mux, mounted[0])
    mux.session_loaded(_SCHEMA_SESSION).add(prefixed_name)
    return mux, host, prefixed_name, mux.children[_SCHEMA_SERVER]


async def test_mount_child_unknown_server(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    assert await mux.mount_child("does-not-exist") == []
    assert mux._start_child.await_count == 0


async def test_start_children_eager_unchanged(tmp_path):
    # "graph-os" is structurally excluded from the catalog -- it is the self
    # identity (served natively, always on), never a mountable fleet child --
    # so this eager-start regression guard exercises two ordinary fleet
    # members instead (CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog).
    mux = _mux_with_children(
        tmp_path,
        {CNT: [(CNT_TOOL, "containers")], "leanix-mcp": [("query", "query")]},
    )
    await mux.start_children()
    names = {t.name for t in mux.aggregated_tools}
    assert CNT_PREFIXED in names
    prefix = get_server_prefix("leanix-mcp")
    assert any(n.startswith(f"{prefix}__") for n in names)
    assert mux._start_child.await_count == 2


# --------------------------------------------------------------------------- #
# Discovery + mapping
# --------------------------------------------------------------------------- #


def _seed_probe(mux, mapping):
    """Pre-populate the self-catalog probe cache. Values are either a list of
    ``(tool, description)`` (reachable) or an error string (unreachable)."""
    for server, val in mapping.items():
        if isinstance(val, str):
            mux._probe_cache[server] = {"tools": [], "error": val}
        else:
            mux._probe_cache[server] = {
                "tools": [
                    {"name": n, "description": d, "inputSchema": {}} for n, d in val
                ],
                "error": None,
            }


def _fake_session(tools):
    sess = AsyncMock()
    res = MagicMock()
    res.tools = [_fake_tool(n, d) for n, d in tools]
    sess.list_tools = AsyncMock(return_value=res)
    return sess


async def test_discover_tools_ranks_and_maps(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(
        mux,
        {
            CNT: [
                (CNT_TOOL, "manage docker containers"),
                ("cm_image_operations", "build images"),
            ],
            "ghost-mcp": [("ghost_op", "manage docker swarm")],  # not in catalog
        },
    )

    discovery = await mux.discover_tools("manage docker containers", top_k=5)
    results = discovery["results"]
    assert results, "expected ranked results"
    top = results[0]
    assert top["tool"] == CNT_TOOL
    assert top["prefixed_name"] == CNT_PREFIXED
    assert top["server"] == CNT
    assert top["mountable"] is True
    assert top["mounted"] is False

    # A tool whose server isn't in this multiplexer's config is flagged unmountable.
    ghost = [r for r in results if r["server"] == "ghost-mcp"]
    assert ghost and ghost[0]["mountable"] is False


async def test_discover_reports_unavailable_servers(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")], "leanix-mcp": []})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(
        mux, {CNT: [(CNT_TOOL, "manage docker")], "leanix-mcp": "timeout after 15s"}
    )

    discovery = await mux.discover_tools("docker", top_k=5)
    assert "leanix-mcp" in discovery["unavailable"]
    assert "timeout" in discovery["unavailable"]["leanix-mcp"]
    assert all(r["server"] != "leanix-mcp" for r in discovery["results"])


async def test_discover_server_level_fallback_on_no_match(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "x")]})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(mux, {CNT: [(CNT_TOOL, "containers")]})

    # Query matches nothing, but the server probed fine → list servers to load.
    discovery = await mux.discover_tools("zzznomatch", top_k=5)
    results = discovery["results"]
    assert results and all(r["tool"] == "*" for r in results)


async def test_discover_all_unreachable_yields_empty_results(tmp_path):
    mux = _mux_with_children(tmp_path, {"leanix-mcp": []})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(mux, {"leanix-mcp": "timeout after 15s"})

    discovery = await mux.discover_tools("anything", top_k=5)
    assert discovery["results"] == []
    assert "leanix-mcp" in discovery["unavailable"]


async def test_probe_catalog_returns_partial_results_within_budget(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")], "slow-mcp": []})

    async def slow_probe(server, **_kwargs):
        if server == CNT:
            return {
                "tools": [{"name": CNT_TOOL, "description": "containers"}],
                "error": None,
            }
        await asyncio.sleep(1)
        return {"tools": [], "error": None}

    mux.probe_server = AsyncMock(side_effect=slow_probe)
    result = await mux.probe_catalog(budget=0.01)

    # Honest, per-server "still working" -- NOT the old fixed "budget
    # exceeded" claim stamped identically on every straggler regardless of
    # its real state (CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog).
    assert result["slow-mcp"]["pending"] is True
    assert "still probing" in result["slow-mcp"]["error"]
    await mux.aclose()  # let the still-running background probe be cancelled cleanly


async def test_slow_server_degrades_alone_fast_servers_unaffected(tmp_path):
    """Requirement: one slow server must not blank the other 60 -- each
    reports its own state with its own reason (CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog)."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")], "slow-mcp": []})

    async def mixed_probe(server, **_kwargs):
        if server == CNT:
            info = {
                "tools": [{"name": CNT_TOOL, "description": "containers"}],
                "error": None,
            }
            mux._probe_cache[server] = info
            return info
        await asyncio.sleep(1)
        info = {"tools": [], "error": None}
        mux._probe_cache[server] = info
        return info

    mux.probe_server = AsyncMock(side_effect=mixed_probe)
    result = await mux.probe_catalog(budget=0.05)

    assert result[CNT]["error"] is None
    assert result[CNT]["tools"][0]["name"] == CNT_TOOL
    assert result["slow-mcp"]["pending"] is True
    await mux.aclose()


async def test_discovery_succeeds_for_a_previously_timed_out_server(tmp_path):
    """The core regression this lane fixes: a server that misses the
    interactive budget must become available on a LATER call once its
    background probe finishes, via cache -- not be re-probed (and time out
    again) from scratch on every subsequent call forever
    (CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog)."""
    mux = _mux_with_children(tmp_path, {"slow-mcp": []})
    release = asyncio.Event()
    calls = 0

    async def eventually_probe(server, **_kwargs):
        nonlocal calls
        calls += 1
        await release.wait()
        info = {
            "tools": [{"name": "slow_op", "description": "eventually works"}],
            "error": None,
        }
        mux._probe_cache[server] = info
        return info

    mux.probe_server = AsyncMock(side_effect=eventually_probe)

    first = await mux.probe_catalog(budget=0.01)
    assert first["slow-mcp"]["pending"] is True
    assert "slow-mcp" not in mux._probe_cache

    # The background probe from the FIRST call is still running (it was
    # never cancelled) -- let it finish now.
    release.set()
    await asyncio.sleep(0.05)

    second = await mux.probe_catalog(budget=0.01)
    assert second["slow-mcp"]["error"] is None
    assert second["slow-mcp"]["tools"][0]["name"] == "slow_op"
    # Probed exactly once: the second call joined the SAME in-flight probe
    # rather than starting a duplicate.
    assert calls == 1


async def test_cached_results_are_labelled_with_age(tmp_path, monkeypatch):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "manage docker containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    fixed_now = 1_000_000.0
    mux._probe_cache[CNT] = {
        "tools": [
            {
                "name": CNT_TOOL,
                "description": "manage docker containers",
                "inputSchema": {},
            }
        ],
        "error": None,
        "probed_at": fixed_now,
    }

    monkeypatch.setattr(time, "time", lambda: fixed_now + 42.0)
    discovery = await mux.discover_tools("manage docker containers", top_k=5)
    top = discovery["results"][0]
    assert top["tool"] == CNT_TOOL
    assert top["age_s"] == 42.0
    assert top["stale"] is False

    cat = await mux.list_catalog(server=CNT)
    assert cat["age_s"] == 42.0


async def test_probe_entry_past_ttl_is_reported_stale(tmp_path, monkeypatch):
    """CONCEPT:AU-ECO.multiplexer.catalog-probe-ttl-staleness — regression for
    the 80-hour-old-cache incident: a normally-settled cache entry (never
    routed through the narrow ``stale`` in-flight flag) must be reported
    ``stale: true`` once its age exceeds the configured TTL. Before this fix,
    ``stale`` echoed a flag that is set in exactly one narrow branch
    (a new probe that itself times out mid-flight) and stayed ``False``
    forever on a settled entry regardless of ``age_s``.

    Exercises the ``include_tools=False`` metadata-only path specifically,
    because it reads ``self._probe_cache`` directly and never calls
    ``probe_catalog`` at all -- the exact path where the dead flag hid
    staleness in production."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    fixed_now = 1_000_000.0
    mux._probe_cache[CNT] = {
        "tools": [{"name": CNT_TOOL, "description": "containers", "inputSchema": {}}],
        "error": None,
        "probed_at": fixed_now,
    }
    monkeypatch.setattr(
        "agent_utilities.core.config.config.mcp_catalog_probe_ttl", 10.0
    )

    # Inside the TTL: honestly fresh.
    monkeypatch.setattr(time, "time", lambda: fixed_now + 5.0)
    fresh = await mux.list_catalog(include_tools=False)
    fresh_entry = next(s for s in fresh["servers"] if s["server"] == CNT)
    assert fresh_entry["stale"] is False

    # 80 hours later (and well past the 10s TTL): must flip to stale, with
    # the real age reported alongside it -- not silently absent, not a bare
    # echo of the dead flag.
    eighty_hours = 80 * 3600.0
    monkeypatch.setattr(time, "time", lambda: fixed_now + eighty_hours)
    stale = await mux.list_catalog(include_tools=False)
    stale_entry = next(s for s in stale["servers"] if s["server"] == CNT)
    assert stale_entry["age_s"] == eighty_hours
    assert stale_entry["stale"] is True


async def test_probe_catalog_retargets_ttl_expired_cache_entries(tmp_path, monkeypatch):
    """CONCEPT:AU-ECO.multiplexer.catalog-probe-ttl-staleness — the other half
    of the fix: a TTL-expired cache entry must actually be RE-PROBED, not
    merely relabelled, so it is refreshed rather than served forever. Before
    this fix ``probe_catalog`` only ever targeted a server completely absent
    from ``_probe_cache``; a settled entry was cached until process exit."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    fixed_now = 1_000_000.0
    mux._probe_cache[CNT] = {
        "tools": [{"name": CNT_TOOL, "description": "containers v1"}],
        "error": None,
        "probed_at": fixed_now,
    }
    monkeypatch.setattr(
        "agent_utilities.core.config.config.mcp_catalog_probe_ttl", 10.0
    )

    calls = 0

    async def refreshed_probe(server, **_kwargs):
        nonlocal calls
        calls += 1
        info = {
            "tools": [{"name": CNT_TOOL, "description": "containers v2"}],
            "error": None,
        }
        return mux._cache_probe(server, info)

    mux.probe_server = AsyncMock(side_effect=refreshed_probe)

    # Still inside the TTL: served as-is, no re-probe triggered.
    monkeypatch.setattr(time, "time", lambda: fixed_now + 5.0)
    fresh = await mux.probe_catalog()
    assert calls == 0
    assert fresh[CNT]["tools"][0]["description"] == "containers v1"

    # Past the TTL: re-targeted for a background-style refresh (this call has
    # no budget, so it awaits the refresh directly) and the cache is updated
    # -- proving the stale entry does not sit there forever.
    monkeypatch.setattr(time, "time", lambda: fixed_now + 11.0)
    refreshed = await mux.probe_catalog()
    assert calls == 1
    assert refreshed[CNT]["tools"][0]["description"] == "containers v2"

    # A caller mid-refresh (budget expires before the re-probe lands) still
    # gets an honest, immediately-available answer -- the last known result,
    # correctly labelled stale -- rather than blocking on the refresh.
    calls = 0
    release = asyncio.Event()

    async def slow_refresh(server, **_kwargs):
        nonlocal calls
        calls += 1
        await release.wait()
        info = {
            "tools": [{"name": CNT_TOOL, "description": "containers v3"}],
            "error": None,
        }
        return mux._cache_probe(server, info)

    mux.probe_server = AsyncMock(side_effect=slow_refresh)
    monkeypatch.setattr(time, "time", lambda: fixed_now + 22.0)  # past the TTL again
    budgeted = await mux.probe_catalog(budget=0.01)
    assert budgeted[CNT]["stale"] is True
    assert (
        budgeted[CNT]["tools"][0]["description"] == "containers v2"
    )  # last known, not blocked

    release.set()
    await mux.aclose()  # let the still-running background refresh be cancelled cleanly


async def test_concurrent_probes_honor_their_own_per_server_deadline(tmp_path):
    """Two servers probed concurrently must each fail/succeed on THEIR OWN
    configured timeout, not a shared ceiling -- and truly concurrently, not
    serialized (CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog)."""
    servers = {
        "quick-timeout-mcp": {
            "command": "python",
            "args": ["-m", "x"],
            "probe_timeout": 0.05,
        },
        "slow-answer-mcp": {
            "command": "python",
            "args": ["-m", "y"],
            "probe_timeout": 5.0,
        },
    }
    mux = multiplexer_from_fixture(_write_config(tmp_path, servers))

    async def _open(server, cfg, stack):
        if server == "quick-timeout-mcp":
            await asyncio.sleep(10)  # always exceeds its OWN 0.05s deadline
        else:
            await asyncio.sleep(0.1)  # comfortably inside its OWN 5s deadline
        return _fake_session([("op", "does a thing")])

    mux._open_one_session = AsyncMock(side_effect=_open)

    t0 = asyncio.get_running_loop().time()
    result = await mux.probe_catalog()  # no shared budget: purely per-server
    elapsed = asyncio.get_running_loop().time() - t0

    assert result["quick-timeout-mcp"]["error"] == "timeout after 0.05s"
    assert result["slow-answer-mcp"]["error"] is None
    # Ran concurrently: total elapsed tracks the SLOWER server (~0.1s), not
    # the sum of both (which would be > 10s if serialized).
    assert elapsed < 2.0
    await mux.aclose()


@pytest.mark.parametrize(
    ("server", "query", "tool", "description"),
    (
        (
            "github-mcp",
            "read and comment on a GitHub pull request",
            "github_pull_requests",
            "Read and comment on GitHub pull requests.",
        ),
        (
            "mattermost-mcp",
            "post a Mattermost message",
            "mattermost_post_message",
            "Post a Mattermost message.",
        ),
    ),
)
async def test_discover_prioritizes_named_server_inside_shared_budget(
    tmp_path,
    monkeypatch,
    server,
    query,
    tool,
    description,
):
    """A named domain is probed before unrelated fleet fan-out consumes the budget."""

    from agent_utilities.core.config import config as agent_config

    tool_map = {
        **{f"slow-{index}-mcp": [] for index in range(24)},
        server: [(tool, description)],
    }
    mux = _mux_with_children(tmp_path, tool_map)
    calls: list[str] = []

    async def staged_probe(server_name, **_kwargs):
        calls.append(server_name)
        if server_name == server:
            info = {
                "tools": [
                    {
                        "name": tool,
                        "description": description,
                        "inputSchema": {},
                    }
                ],
                "error": None,
            }
            mux._probe_cache[server_name] = info
            return info
        await asyncio.sleep(1)
        return {"tools": [], "error": None}

    monkeypatch.setattr(agent_config, "mcp_dynamic_discovery_timeout", 0.05)
    mux.probe_server = AsyncMock(side_effect=staged_probe)

    discovery = await mux.discover_tools(query, top_k=5)

    assert calls[0] == server
    assert discovery["results"][0]["server"] == server
    assert discovery["results"][0]["tool"] == tool
    assert server not in discovery["unavailable"]


async def test_discover_marks_mounted(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    await mux.mount_child(CNT)  # mounted server is probed from live tools
    mux._exposed.add(CNT_PREFIXED)

    discovery = await mux.discover_tools("containers", top_k=5)
    assert discovery["results"][0]["mounted"] is True


# --------------------------------------------------------------------------- #
# Self-catalog probe
# --------------------------------------------------------------------------- #


async def test_probe_server_success_and_caches(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")]})

    async def _open(server, cfg, stack):
        return _fake_session([(CNT_TOOL, "manage containers")])

    mux._open_one_session = AsyncMock(side_effect=_open)
    info = await mux.probe_server(CNT)
    assert info["error"] is None
    assert info["tools"][0]["name"] == CNT_TOOL
    assert mux._probe_cache[CNT] is info  # cached


async def test_probe_server_records_unreachable(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")]})

    async def _boom(server, cfg, stack):
        raise OSError("connection refused")

    mux._open_one_session = AsyncMock(side_effect=_boom)
    info = await mux.probe_server(CNT, timeout=1)
    assert info["tools"] == []
    assert info["error"] == "OSError: connection refused"


async def test_probe_server_uses_live_tools_when_mounted(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    await mux.mount_child(CNT)
    # Should NOT reconnect for an already-mounted child.
    mux._open_one_session = AsyncMock(side_effect=AssertionError("must not reconnect"))
    info = await mux.probe_server(CNT)
    assert info["error"] is None
    assert info["tools"][0]["name"] == CNT_TOOL


async def test_probe_server_preserves_an_mcp_apps_tool_descriptor_meta(tmp_path):
    """BUG-071: an MCP Apps tool's ``_meta.ui.resourceUri`` (the
    ``io.modelcontextprotocol/ui`` extension) is declared on the TOOL
    DESCRIPTOR, so it must survive the mounted-child probe path
    (``_live_tools_for_server``) that ``webui_mcp_delegation._list_mcp_server_tools``
    reads — this was previously dropped even though ``tool_object()`` already
    carries it (``_prefixed_child_tools`` copies ``tool.meta`` when mounting).
    """
    ui_meta = {
        "ui": {
            "resourceUri": "ui://graph-os/task-progress.html",
            "visibility": ["model"],
        }
    }
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})

    async def fake_start_child(server_name, cfg):
        tools = [_fake_tool(CNT_TOOL, "containers", meta=ui_meta)]
        session = AsyncMock()
        return server_name, session, tools, cfg

    mux._start_child = AsyncMock(side_effect=fake_start_child)
    await mux.mount_child(CNT)
    mux._open_one_session = AsyncMock(side_effect=AssertionError("must not reconnect"))

    info = await mux.probe_server(CNT)

    assert info["error"] is None
    assert info["tools"][0]["name"] == CNT_TOOL
    assert info["tools"][0]["meta"] == ui_meta


async def test_live_tools_for_server_omits_meta_key_when_absent(tmp_path):
    """An ordinary (non-Apps) tool's dict shape is unchanged (BUG-071 is additive)."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "containers")]})
    await mux.mount_child(CNT)

    info = await mux.probe_server(CNT)

    assert "meta" not in info["tools"][0]


# --------------------------------------------------------------------------- #
# Catalog browse
# --------------------------------------------------------------------------- #


async def test_list_catalog_all_servers(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")], "leanix-mcp": []})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(
        mux,
        {
            CNT: [(CNT_TOOL, "containers"), ("cm_image_operations", "images")],
            "leanix-mcp": "timeout after 15s",
        },
    )

    cat = await mux.list_catalog()
    assert cat["total_servers"] == 2
    assert cat["total_tools"] == 2  # only the reachable server's tools
    assert "leanix-mcp" in cat["unavailable"]

    by_name = {s["server"]: s for s in cat["servers"]}
    assert by_name[CNT]["tool_count"] == 2
    assert by_name[CNT]["available"] is True
    assert CNT_PREFIXED in by_name[CNT]["tools"]  # prefixed names in all-view
    assert by_name["leanix-mcp"]["available"] is False
    assert "error" in by_name["leanix-mcp"]


async def test_list_catalog_single_server_drilldown(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")]})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(mux, {CNT: [(CNT_TOOL, "manage containers")]})

    cat = await mux.list_catalog(server=CNT)
    assert cat["server"] == CNT
    assert cat["available"] is True
    tool = cat["tools"][0]
    assert tool["prefixed_name"] == CNT_PREFIXED
    assert tool["tool"] == CNT_TOOL
    assert tool["description"] == "manage containers"


@pytest.mark.spec("GRAPHOS-FLEET-R029")
async def test_list_catalog_unknown_server(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "c")]})
    mux._kg_call = AsyncMock(return_value=None)
    _seed_probe(mux, {CNT: [(CNT_TOOL, "c")]})
    cat = await mux.list_catalog(server="does-not-exist")
    assert "error" in cat


# --------------------------------------------------------------------------- #
# Unique prefixes (collision-free at any scale)
# --------------------------------------------------------------------------- #


def test_server_prefix_resolves_derived_collisions(tmp_path):
    # foo-bar-mcp and foo-baz-mcp both auto-derive the acronym "fb" → the
    # resolver must hand one a distinct prefix.
    mux = _mux_with_children(
        tmp_path,
        {"foo-bar-mcp": [("a", "")], "foo-baz-mcp": [("b", "")]},
    )
    p1 = mux.server_prefix("foo-bar-mcp")
    p2 = mux.server_prefix("foo-baz-mcp")
    assert p1 != p2
    assert "fb" in (p1, p2)  # one keeps the preferred prefix
    prefixes = [mux.server_prefix(s) for s in mux.load_catalog()]
    assert len(prefixes) == len(set(prefixes)), "prefixes must be unique"


def test_config_prefix_override_wins(tmp_path):
    mux = _mux_with_children(tmp_path, {"foo-bar-mcp": [("a", "")]})
    mux.load_catalog()["foo-bar-mcp"]["prefix"] = "myfoo"
    mux._build_prefix_map()
    assert mux.server_prefix("foo-bar-mcp") == "myfoo"


def test_reverse_prefix_resolves_owner(tmp_path):
    mux = _mux_with_children(
        tmp_path,
        {"foo-bar-mcp": [("a", "")], "foo-baz-mcp": [("b", "")]},
    )
    for name in ["foo-bar-mcp", "foo-baz-mcp"]:
        prefix = mux.server_prefix(name)
        assert mux._server_for_prefixed(f"{prefix}__x") == name


# --------------------------------------------------------------------------- #
# Enabled / disabled annotation
# --------------------------------------------------------------------------- #


def _seed_disabled(mux, server, disabled):
    mux.load_catalog()[server]["disabledTools"] = disabled


def _mux_with_one_disabled_tool(tmp_path):
    """A mux with ``CNT`` mounted, ``cm_info_operations`` disabled, and the
    KG call stubbed out -- the shared setup for the catalog/discovery tests
    below, which each seed their own probe cache."""
    mux = _mux_with_children(
        tmp_path, {CNT: [(CNT_TOOL, "a"), ("cm_info_operations", "b")]}
    )
    _seed_disabled(mux, CNT, ["cm_info_operations"])
    mux._kg_call = AsyncMock(return_value=None)
    return mux


async def test_list_catalog_splits_enabled_disabled(tmp_path):
    mux = _mux_with_one_disabled_tool(tmp_path)
    _seed_probe(mux, {CNT: [(CNT_TOOL, "containers"), ("cm_info_operations", "info")]})

    cat = await mux.list_catalog()
    s = {x["server"]: x for x in cat["servers"]}[CNT]
    assert s["tool_count"] == 2
    assert s["enabled_count"] == 1
    assert CNT_PREFIXED in s["tools"]
    assert CNT_INFO_PREFIXED in s.get("disabled_tools", [])
    assert CNT_INFO_PREFIXED not in s["tools"]


async def test_list_catalog_drilldown_enabled_flag(tmp_path):
    mux = _mux_with_one_disabled_tool(tmp_path)
    _seed_probe(mux, {CNT: [(CNT_TOOL, "c"), ("cm_info_operations", "i")]})

    cat = await mux.list_catalog(server=CNT)
    by_tool = {t["tool"]: t for t in cat["tools"]}
    assert by_tool[CNT_TOOL]["enabled"] is True
    assert by_tool["cm_info_operations"]["enabled"] is False


async def test_discover_skips_disabled_tools(tmp_path):
    mux = _mux_with_one_disabled_tool(tmp_path)
    _seed_probe(
        mux,
        {CNT: [(CNT_TOOL, "manage docker"), ("cm_info_operations", "manage docker")]},
    )

    discovery = await mux.discover_tools("manage docker", top_k=10)
    tools = [r["tool"] for r in discovery["results"]]
    assert CNT_TOOL in tools
    assert "cm_info_operations" not in tools


# --------------------------------------------------------------------------- #
# Probe error formatting
# --------------------------------------------------------------------------- #


def test_format_probe_error_unwraps_exceptiongroup():
    from graph_os.fleet.multiplexer import _format_probe_error

    eg = ExceptionGroup(
        "boom",
        [RuntimeError("HTTP 502 Bad Gateway"), RuntimeError("HTTP 502 Bad Gateway")],
    )
    msg = _format_probe_error(eg)
    # The leaf type AND its message are preserved (deduped across identical
    # leaves) — a bare "RuntimeError" gives no diagnostic signal (this exact
    # anti-pattern made the whole MCP fleet's outage undiagnosable).
    assert msg == "RuntimeError: HTTP 502 Bad Gateway"
    assert _format_probe_error(ConnectionError("refused")) == "ConnectionError: refused"
    assert _format_probe_error(RuntimeError("")) == "RuntimeError"


# --------------------------------------------------------------------------- #
# load/unload resolution
# --------------------------------------------------------------------------- #


async def test_resolve_and_mount_by_server(tmp_path):
    mux = _mux_with_children(
        tmp_path, {CNT: [(CNT_TOOL, "a"), ("cm_image_operations", "b")]}
    )
    servers, to_expose, failed = await mux.resolve_and_mount(servers=[CNT])
    assert servers == [CNT]
    assert set(to_expose) == {CNT_PREFIXED, CNT_IMAGE_PREFIXED}
    assert failed == {}
    assert CNT in mux.children


async def test_resolve_and_mount_by_prefixed_tool(tmp_path):
    mux = _mux_with_children(
        tmp_path, {CNT: [(CNT_TOOL, "a"), ("cm_image_operations", "b")]}
    )
    # Request a single tool by its prefixed name (server derived from prefix).
    servers, to_expose, failed = await mux.resolve_and_mount(tools=[CNT_PREFIXED])
    assert servers == [CNT]
    assert to_expose == [CNT_PREFIXED]  # only the requested subset
    assert failed == {}


async def test_resolve_and_mount_skips_already_exposed(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    await mux.resolve_and_mount(servers=[CNT])
    mux._exposed.add(CNT_PREFIXED)
    _servers, to_expose, _failed = await mux.resolve_and_mount(servers=[CNT])
    assert to_expose == []


async def test_resolve_and_mount_reports_failed(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")], "leanix-mcp": []})
    good = mux._start_child.side_effect

    async def _fail_leanix(server_name, cfg):
        if server_name == "leanix-mcp":
            return None  # mount fails
        return await good(server_name, cfg)

    mux._start_child = AsyncMock(side_effect=_fail_leanix)
    _seed_probe(mux, {"leanix-mcp": "timeout after 15s"})  # reason for the failure

    mounted, to_expose, failed = await mux.resolve_and_mount(
        servers=[CNT, "leanix-mcp"]
    )
    assert mounted == [CNT]
    assert CNT_PREFIXED in to_expose
    assert "leanix-mcp" in failed and "timeout" in failed["leanix-mcp"]


def test_forget_tool(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    mux.tool_to_server[CNT_PREFIXED] = (CNT, CNT_TOOL)
    mux.aggregated_tools.append(_fake_tool(CNT_PREFIXED))
    mux._exposed.add(CNT_PREFIXED)

    owner = mux.forget_tool(CNT_PREFIXED)
    assert owner == CNT
    assert CNT_PREFIXED not in mux.tool_to_server
    assert CNT_PREFIXED not in mux._exposed
    assert CNT_PREFIXED not in {t.name for t in mux.aggregated_tools}


def test_server_for_prefixed_reverses_prefix(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    # Not yet mounted: resolved purely from the prefix → catalog.
    assert mux._server_for_prefixed(CNT_PREFIXED) == CNT


# --------------------------------------------------------------------------- #
# FastMCP meta-tool wiring (end-to-end through a real FastMCP instance)
# --------------------------------------------------------------------------- #


async def _registered_tool_names(mcp) -> set[str]:
    tools = await mcp.list_tools()
    return {t.name for t in tools}


def test_prefix_sanity():
    # Guards the (server -> prefix) assumption the rest of the suite relies on.
    assert get_server_prefix(CNT) == "cm"


# --------------------------------------------------------------------------- #
# Verbose tools held in catalog (load on demand) — CONCEPT:AU-ECO.mcp.tool-mode-standardization
# --------------------------------------------------------------------------- #
def test_tool_is_verbose_tag_detection():
    assert _tool_is_verbose(_fake_tool("x", tags=["graph_write", "verbose"])) is True
    assert _tool_is_verbose(_fake_tool("x", tags=["write"])) is False
    assert _tool_is_verbose(_fake_tool("x")) is False  # no meta -> not verbose


@pytest.mark.asyncio
async def test_always_on_holds_verbose_tools_in_catalog(tmp_path):
    """An always-on child's verbose tools are mounted (in the catalog, loadable)
    but NOT auto-exposed at boot — only the condensed surface is.

    "graph-os" itself is structurally excluded from the catalog (self,
    served natively) so this uses an ordinary fleet member to exercise the
    same condensed/verbose boot filter."""
    from fastmcp import FastMCP

    server = "example-mcp"
    mux = _mux_with_children(tmp_path, {server: []})

    async def fake_start_child(server_name, cfg):
        tools = [
            _fake_tool("graph_write", "condensed", tags=["example-mcp", "write"]),
            _fake_tool(
                "graph_write_add_node", "verbose", tags=["graph_write", "verbose"]
            ),
        ]
        return server_name, AsyncMock(), tools, cfg

    mux._start_child = AsyncMock(side_effect=fake_start_child)

    mcp = FastMCP("mux")
    tools = await mux.mount_child(server)
    # Replicate the dynamic always-on boot filter:
    for tool in tools:
        if _tool_is_verbose(tool):
            continue
        _register_forwarder(mcp, mux, tool)

    prefixes = {t.name for t in mux.aggregated_tools}
    # Both tools are mounted/aggregated (in the catalog → loadable on demand)
    assert any("graph_write_add_node" in n for n in prefixes)
    # …but only the condensed one is auto-exposed; the verbose one is held back.
    exposed = mux._exposed
    assert any(n.endswith("graph_write") for n in exposed)
    assert not any("graph_write_add_node" in n for n in exposed)


async def test_server_load_holds_verbose(tmp_path):
    """CONCEPT:AU-ECO.mcp.tool-mode-standardization — load_tools(servers=[X]) exposes only X's condensed tools; verbose
    1:1 tools stay loadable only by EXPLICIT name, so a server-load never floods context."""
    servers = {"svc": {"command": "python", "args": ["-m", "svc"]}}
    mux = multiplexer_from_fixture(_write_config(tmp_path, servers))

    async def fake_start_child(server_name, cfg):
        tools = [
            _fake_tool("svc_action", "condensed action-routed"),
            _fake_tool("svc_get_thing", "verbose 1:1", tags=["verbose"]),
        ]
        return server_name, AsyncMock(), tools, cfg

    mux._start_child = AsyncMock(side_effect=fake_start_child)

    _s, to_expose, _f = await mux.resolve_and_mount(servers=["svc"])
    cond = [n for n in to_expose if n.endswith("action")]
    verb = [n for n in to_expose if n.endswith("get_thing")]
    assert cond and not verb  # condensed exposed, verbose held

    # verbose IS loadable by explicit name
    verbose_name = next(n for n in mux.tool_to_server if n.endswith("get_thing"))
    _s2, to_expose2, _f2 = await mux.resolve_and_mount(tools=[verbose_name])
    assert verbose_name in to_expose2


# --------------------------------------------------------------------------- #
# Control-plane truthfulness (lane-mcp-desync): reported "mounted"/"loaded"
# state must derive from the SAME predicate the dispatch gate enforces, never
# from a parallel bookkeeping structure that can drift from it.
# --------------------------------------------------------------------------- #


async def test_resolve_and_mount_reports_unknown_tool_name_as_failed(tmp_path):
    """A prefixed name that resolves to no configured server must never be
    silently dropped from both ``to_expose`` and ``failed`` — the caller needs
    to know exactly which of its requested names didn't make it."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    mounted, to_expose, failed = await mux.resolve_and_mount(
        tools=["zz__totally_bogus"]
    )
    assert mounted == []
    assert to_expose == []
    assert "zz__totally_bogus" in failed


async def test_resolve_and_mount_reports_disabled_requested_tool_as_failed(tmp_path):
    """A tool explicitly requested by name, whose owning server mounts fine
    but that config disables, must show up in ``failed`` — not vanish while
    ``mounted_servers`` still claims success."""
    mux = _mux_with_children(
        tmp_path, {CNT: [(CNT_TOOL, "a"), ("cm_info_operations", "b")]}
    )
    _seed_disabled(mux, CNT, ["cm_info_operations"])

    mounted, to_expose, failed = await mux.resolve_and_mount(tools=[CNT_INFO_PREFIXED])

    assert mounted == [CNT]
    assert CNT_INFO_PREFIXED not in to_expose
    assert CNT_INFO_PREFIXED in failed


async def test_tool_dispatchable_false_for_catalogued_but_unmounted_tool(tmp_path):
    """A tool the catalog KNOWS about (via the prefix map) but that has never
    been mounted/exposed must report as NOT dispatchable — the optimistic
    'not gated -> visible' fallback is only valid for tools the multiplexer's
    own bookkeeping has no opinion on at all (e.g. native host tools)."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    assert mux.tool_dispatchable(CNT_PREFIXED, session_key="any-session") is False


def test_tool_dispatchable_false_for_unknown_name_on_a_serving_instance(
    tmp_path,
):
    """An unrecognized native host tool must be DENIED by default, even on a
    REAL, serving instance (non-empty catalog) — a name that matches no
    server, no global/local visibility set, and no exposed forwarder is not
    something this bookkeeping can vouch for, so it must not be presumed
    callable. Default-open here was exactly the gap a caller could use to
    reach an unregistered host tool the real dispatch middleware never
    actually admitted.

    The EG-backed catalog source (``tests.fleet.catalog_fixture``) composes
    the catalog synchronously at construction, unlike the retired static
    ``MCP_CONFIG`` path that deferred the first read to ``load_catalog()`` --
    so a freshly-built fixture with an admissible server is already serving."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    assert mux.is_serving() is True  # at least one real server catalogued
    assert mux.tool_dispatchable("some_native_host_tool") is False


def test_tool_dispatchable_false_for_unknown_name_on_a_non_serving_instance(tmp_path):
    """D-SH-6 (reports/deferred/lane-skill-harvest.md): a freshly-constructed
    multiplexer whose catalog resolves to ZERO servers (what a fleet
    harvest/source_sync builds standalone) must not assert callability for a
    name unknown to its bookkeeping — verified live in-pod to return True for
    BOTH a real probed tool name and an invented one, which is exactly the
    mounted-vs-callable lie the reconciliation gate exists to close."""
    mux = multiplexer_from_fixture(_write_config(tmp_path, {}))  # no servers at all
    assert mux.is_serving() is False
    assert mux.tool_dispatchable("servicenow_servicenow_account") is False
    assert mux.tool_dispatchable("any_other_invented_name") is False
    # Still False even after the load_catalog() side effect _server_for_prefixed
    # triggers internally -- the catalog it produces is genuinely empty.
    assert mux.is_serving() is False


async def test_tool_dispatchable_is_session_scoped_after_expose(tmp_path):
    """Reproduces the reported parent-vs-subagent asymmetry at the state layer:
    once a tool is globally exposed (registered as a live forwarder), it is
    dispatchable ONLY for the session(s) that actually loaded it — a second,
    brand-new session must see it as not-yet-dispatchable even though the
    forwarder already exists process-globally."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "a")]})
    _mounted, to_expose, failed = await mux.resolve_and_mount(servers=[CNT])
    assert failed == {}
    for name in to_expose:
        mux._exposed.add(name)  # simulates _register_forwarder's bookkeeping

    mux.session_loaded("session-A").add(CNT_PREFIXED)

    assert mux.tool_dispatchable(CNT_PREFIXED, session_key="session-A") is True
    assert mux.tool_dispatchable(CNT_PREFIXED, session_key="session-B") is False


def test_local_session_meta_cannot_override_an_authenticated_http_session(
    monkeypatch,
):
    """D-W2-6 trust-boundary proof: the caller-declared local-session id
    (``_LOCAL_SESSION_META_KEY``) must NEVER be consulted once a real,
    authenticated HTTP request context exists — otherwise an HTTP caller could
    simply declare another session's id in its own request ``_meta`` and
    inherit that session's loaded tools. This is why
    :func:`graph_os.fleet.multiplexer._explicit_local_session_key` is only
    ever consulted from the non-HTTP except-arms of ``_session_key`` — never
    from the HTTP try-body — so scoping this correlator can only ever ADD
    isolation between local, same-process callers; it can never be used to
    cross the HTTP trust boundary.
    """
    mock_request = MagicMock()
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_request",
        lambda: mock_request,
    )
    mock_context = MagicMock()
    mock_context.session_id = "real-authenticated-http-session"
    mock_context.request_context.meta = {
        _LOCAL_SESSION_META_KEY: "someone-elses-declared-session"
    }
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_context",
        lambda: mock_context,
    )

    assert _session_key() == "real-authenticated-http-session"


async def test_notify_tools_changed_returns_false_without_request_context():
    from graph_os.fleet.multiplexer import _notify_tools_changed

    sent = await _notify_tools_changed(None)
    assert sent is False


@pytest.mark.spec("GRAPHOS-DEPLOY-R004")
async def test_notify_tools_changed_surfaces_send_failure(monkeypatch, caplog):
    """A LIVE client that rejects/drops the push must be visible via the
    return value (and logged with its real cause) — never swallowed into
    silence the caller can't observe (CLAUDE.md: never swallow an exception)."""
    import logging

    from graph_os.fleet.multiplexer import _notify_tools_changed

    class _FailingContext:
        async def send_notification(self, _note):
            raise RuntimeError("client transport gone")

    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_context", lambda: _FailingContext()
    )

    with caplog.at_level(logging.WARNING, logger="mcp_multiplexer"):
        sent = await _notify_tools_changed(None)

    assert sent is False
    assert any("list_changed" in r.message for r in caplog.records)


def test_load_tools_field_contract_never_reintroduces_notified(tmp_path):
    """BUG-050 contract guard: the field name is retired, not merely renamed at
    one call site. Grepping the source is the cheapest durable guard against a
    future edit re-adding ``"notified":`` (e.g. a copy-pasted branch) — a
    structural check that survives independent of any single test path."""
    import inspect

    from graph_os.fleet import multiplexer

    source = inspect.getsource(multiplexer)
    assert '"notified"' not in source
    assert "notified: NotRequired[bool]" not in source


async def test_forced_reprobe_does_not_evict_a_live_joinable_probe(tmp_path):
    """A forced re-probe runs as its OWN task; when it settles it must not
    retract a DIFFERENT, still-running non-forced probe from the join map.

    Regression: ``_settle_probe_task`` popped ``_probe_inflight[server]``
    unconditionally, so a fast forced probe finishing while a slow shared probe
    was still running left that shared probe untracked -- a later call spawned a
    duplicate of an already-in-flight probe, and ``aclose`` no longer knew to
    cancel it.
    """
    mux = _mux_with_children(tmp_path, {"slow-mcp": []})
    release = asyncio.Event()

    async def probe(server, force=False, timeout=None):
        if not force:
            await release.wait()
        return {"tools": [], "error": None}

    mux.probe_server = AsyncMock(side_effect=probe)

    shared = mux._ensure_probing("slow-mcp", force=False, timeout=None)
    forced = mux._ensure_probing("slow-mcp", force=True, timeout=None)
    assert forced is not shared
    await forced

    # The slow shared probe is still running and still joinable.
    assert mux._probe_inflight.get("slow-mcp") is shared
    assert mux._ensure_probing("slow-mcp", force=False, timeout=None) is shared

    release.set()
    await shared
    assert "slow-mcp" not in mux._probe_inflight
    await mux.aclose()


async def test_aclose_cancels_a_forced_probe_too(tmp_path):
    """``aclose`` must cancel EVERY live probe, not just the joinable ones --
    a forced re-probe is never in ``_probe_inflight`` and used to outlive the
    multiplexer that spawned it."""
    mux = _mux_with_children(tmp_path, {"slow-mcp": []})

    async def never_finishes(server, force=False, timeout=None):
        await asyncio.Event().wait()
        return {"tools": [], "error": None}

    mux.probe_server = AsyncMock(side_effect=never_finishes)

    forced = mux._ensure_probing("slow-mcp", force=True, timeout=None)
    await asyncio.sleep(0)
    assert "slow-mcp" not in mux._probe_inflight  # forced probes are not joinable
    assert forced in mux._probe_tasks

    await mux.aclose()
    assert forced.cancelled()
    assert not mux._probe_tasks


def _mock_authenticated_http_caller(monkeypatch, *, scopes):
    """Simulate a real, authenticated HTTP/remote caller with exactly ``scopes``."""
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_request", lambda: SimpleNamespace()
    )
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_access_token",
        lambda: SimpleNamespace(scopes=list(scopes), claims=None),
    )


def test_fleet_required_capabilities_rejects_generic_admin_for_discover_and_delegate():
    """A broad ``admin``/``kg:admin``/``mcp:admin`` grant is a different
    capability than the fleet's own ``mcp:discover``/``mcp:delegate`` scopes
    and must never satisfy either of them. ``manage`` has no narrower
    fleet-specific scope of its own, so an administrative grant remains its
    exact requirement."""
    for admin_scope in ("admin", "kg:admin", "mcp:admin"):
        assert admin_scope not in _fleet_required_capabilities("discover")
        assert admin_scope not in _fleet_required_capabilities("delegate")
        assert admin_scope in _fleet_required_capabilities("manage")


def test_require_fleet_capability_denies_admin_only_caller_for_discover(monkeypatch):
    """The previously over-broad path, now denied: an admin-only caller who
    holds no ``mcp:discover`` scope must be refused fleet discovery -- a
    generic administrative grant is not a substitute for the exact fleet
    scope the requested action declares."""
    _mock_authenticated_http_caller(monkeypatch, scopes=["admin"])
    with pytest.raises(ToolError, match="MCP fleet discover capability required"):
        _require_fleet_capability("discover")


def test_require_fleet_capability_allows_exact_scope_caller_for_discover(monkeypatch):
    """The correctly scoped path, still allowed: a caller holding the fleet's
    own ``mcp:discover`` scope passes with no administrative grant at all."""
    _mock_authenticated_http_caller(monkeypatch, scopes=["mcp:discover"])
    _require_fleet_capability("discover")  # must not raise


def test_require_fleet_capability_enforces_extra_scopes_even_for_admin_caller(
    monkeypatch,
):
    """A child's own declared extra-scope requirement must still be enforced
    even when the caller additionally holds an administrative capability --
    admin no longer bypasses a declared child capability-scope requirement."""
    _mock_authenticated_http_caller(monkeypatch, scopes=["mcp:delegate", "admin"])
    with pytest.raises(ToolError, match="Child MCP capability scope required"):
        _require_fleet_capability("delegate", extra_scopes=["child:special"])


def test_require_fleet_capability_allows_caller_holding_the_extra_scope(monkeypatch):
    _mock_authenticated_http_caller(
        monkeypatch, scopes=["mcp:delegate", "child:special"]
    )
    _require_fleet_capability("delegate", extra_scopes=["child:special"])  # no raise


def test_require_fleet_capability_denies_stdio_caller_with_no_verified_actor():
    """GRAPHOS-FLEET-R021: a local stdio caller is no longer trusted outright
    -- with no verified process actor bound at all, the exact same check
    applies and the call is refused, exactly as an unauthenticated remote
    caller would be."""
    with pytest.raises(ToolError, match="Authenticated local process context"):
        _require_fleet_capability("discover")


def test_require_fleet_capability_denies_stdio_caller_missing_the_exact_scope():
    """A verified stdio actor that lacks the fleet's own ``mcp:discover``
    scope is refused the same way a remote caller without it would be --
    being local carries no implicit discovery/delegation grant."""
    from agent_utilities.security.brain_context import ActorContext, use_actor

    actor = ActorContext(
        actor_id="stdio-caller",
        roles=("some:other:scope",),
        tenant_id="test-tenant",
        authenticated=True,
    )
    with use_actor(actor):
        with pytest.raises(ToolError, match="MCP fleet discover capability required"):
            _require_fleet_capability("discover")


def test_require_fleet_capability_allows_stdio_caller_with_exact_scope():
    """The correctly scoped stdio path: a verified local actor holding the
    fleet's own ``mcp:discover`` scope passes -- the same outcome an
    equally-scoped remote caller gets, proving local stdio runs through the
    identical check rather than a separate, more permissive one."""
    from agent_utilities.security.brain_context import ActorContext, use_actor

    actor = ActorContext(
        actor_id="stdio-caller",
        roles=("mcp:discover",),
        tenant_id="test-tenant",
        authenticated=True,
    )
    with use_actor(actor):
        _require_fleet_capability("discover")  # must not raise
