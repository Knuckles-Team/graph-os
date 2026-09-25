"""Governed native reads never use a service credential or stale policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.fleet.catalog_items import CatalogItem
from graph_os.fleet.native_reads import NativeReadGateway


@pytest.mark.asyncio
async def test_delegated_native_read_requires_scopes_policy_and_mode():
    item = CatalogItem(
        "fleet:resource:s/data://entry",
        "resource",
        "data://entry",
        server="s",
        required_scopes=frozenset({"data:read"}),
    )
    caller = SimpleNamespace(effective_scopes=frozenset({"mcp:delegate", "data:read"}))
    mode = ["delegated"]
    allowed = [True]
    calls = []

    async def credential_mode(_item, _caller):
        return mode[0]

    async def policy_check(_item, _caller):
        return allowed[0]

    async def delegated_read(_item, params, actual_caller):
        calls.append((dict(params), actual_caller))
        return "Record"

    gateway = NativeReadGateway(
        credential_mode=credential_mode,
        policy_check=policy_check,
        delegated_read=delegated_read,
    )
    assert await gateway.read(item, {"uri": item.name}, caller) == "Record"
    assert calls == [({"uri": item.name}, caller)]
    mode[0] = "service"
    with pytest.raises(PermissionError, match="delegated credential"):
        await gateway.read(item, {}, caller)
    mode[0] = "delegated"
    allowed[0] = False
    with pytest.raises(PermissionError, match="policy"):
        await gateway.read(item, {}, caller)
    allowed[0] = True
    without_child_scope = SimpleNamespace(effective_scopes=frozenset({"mcp:delegate"}))
    with pytest.raises(PermissionError, match="scopes"):
        await gateway.read(item, {}, without_child_scope)
    assert len(calls) == 1
