"""MCP and REST projection for the GraphOS browser-control authority."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from graph_os.browser_control.browser_control_runtime import (
    BrowserControlAction,
    dispatch_browser_control,
)


def register_browser_control_tools(mcp: Any) -> None:
    """Register one action router shared by the MCP and REST projections."""

    @mcp.tool(
        name="browser_control",
        description=(
            "Drive an attended agent-webui document through GraphOS authority. "
            "Actions: issue_lease, execute_call, cancel_call, reconcile_call, "
            "revoke_lease."
        ),
        tags={"graph-os", "browser-control", "webmcp"},
    )
    async def browser_control(
        action: BrowserControlAction,
        payload: Annotated[
            dict[str, Any],
            Field(description="Exact request object for the selected action."),
        ],
    ) -> str:
        receipt = await dispatch_browser_control(action, payload)
        return receipt.model_dump_json()

    from graph_os.mcp_server import runtime

    runtime.REGISTERED_TOOLS["browser_control"] = browser_control


__all__ = ["register_browser_control_tools"]
