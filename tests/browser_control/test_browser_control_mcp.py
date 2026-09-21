"""MCP registration and REST-parity contract for browser control."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.browser_control import mcp as browser_control_mcp
from graph_os.browser_control.browser_control_api import BrowserLeaseReceipt
from graph_os.mcp_server import runtime


def _recording_mcp(tools: dict[str, Any]) -> SimpleNamespace:
    def tool(*, name: str, **_kwargs: Any) -> Any:
        def decorate(function: Any) -> Any:
            tools[name] = function
            return function

        return decorate

    return SimpleNamespace(tool=tool)


@pytest.mark.asyncio
async def test_browser_control_registers_one_mcp_rest_workflow_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(action: str, payload: dict[str, Any]) -> BrowserLeaseReceipt:
        seen.append((action, payload))
        return BrowserLeaseReceipt(
            lease_id="browserlease_0123456789abcdef0123456789abcdef",
            document_ref=str(payload["document_ref"]),
            tool_ids=tuple(payload["tool_ids"]),
            registration_generation=1,
            expires_at=100.0,
            hard_expires_at=200.0,
            status="active",
        )

    monkeypatch.setattr(browser_control_mcp, "dispatch_browser_control", dispatch)
    tools: dict[str, Any] = {}
    mcp = _recording_mcp(tools)
    prior_tool = runtime.REGISTERED_TOOLS.get("browser_control")
    try:
        browser_control_mcp.register_browser_control_tools(mcp)
        assert runtime.REGISTERED_TOOLS["browser_control"] is tools["browser_control"]
        assert runtime.ACTION_TOOL_ROUTES["browser_control"] == "/browser/control"
        raw = await tools["browser_control"](
            action="issue_lease",
            payload={
                "document_ref": "document_" + "a" * 64,
                "tool_ids": ["agent-webui.get-page-context"],
                "attended": True,
            },
        )
        assert json.loads(raw)["status"] == "active"
        assert seen == [
            (
                "issue_lease",
                {
                    "document_ref": "document_" + "a" * 64,
                    "tool_ids": ["agent-webui.get-page-context"],
                    "attended": True,
                },
            )
        ]
    finally:
        if prior_tool is None:
            runtime.REGISTERED_TOOLS.pop("browser_control", None)
        else:
            runtime.REGISTERED_TOOLS["browser_control"] = prior_tool
