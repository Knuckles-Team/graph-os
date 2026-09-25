"""Eunomia per-caller policy on the served graph-os MCP surface (EH-629).

The SDK server factory ships no Eunomia middleware, so graph-os installs its
own. The policy decision point is :mod:`graph_os.mcp_server.policy_decision`
(embedded policy file or remote PDP); this module supplies the graph-os caller
and the tool semantics:

* **Narrowing only.** The filter runs on top of authentication, the exact
  fleet scopes and session visibility. It can remove a tool or refuse a call;
  it never adds one.
* **List and call agree.** A tool is listed and callable only when the policy
  allows both ``list`` and ``execute`` on the same resource for the same
  principal, so ``tools/list`` and ``tools/call`` evaluate identical requests.
* **Fail closed.** A caller with no verified identity sees no tools. When the
  PDP is unreachable, the remote bridge answers every check with a denial, so
  the list is empty and every call is refused with ``POLICY_UNAVAILABLE``.
  A policy file or endpoint that cannot be loaded stops the server at startup.
* **Off is not unfiltered.** With ``EUNOMIA_TYPE=none`` no filter is built;
  the surface is still filtered by the exact fleet scopes
  (:class:`graph_os.fleet.multiplexer.SessionVisibilityMiddleware`) and every
  native call still runs under the caller's own EG authority.

The same decision guards the native REST twins and in-process delegation,
because :func:`authorize_native_call` runs inside ``runtime._execute_tool``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from eunomia_core import schemas
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from graph_os.fleet.fleet_authority import FleetCaller, resolve_fleet_caller
from graph_os.mcp_server.policy_decision import (
    UNAVAILABLE_REASON,
    EmbeddedPolicy,
    PolicyDecisionPoint,
    RemotePolicy,
    load_policy_file,
)

__all__ = [
    "TOOL_ACTIONS",
    "EunomiaMode",
    "ServedPolicyFilter",
    "authorize_native_call",
    "build_policy_filter",
    "install_policy_filter",
    "served_policy_middlewares",
]

#: Both actions gate a tool, for listing and for calling alike.
TOOL_ACTIONS: tuple[str, ...] = ("list", "execute")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")


class EunomiaMode(StrEnum):
    """``EUNOMIA_TYPE``: an unknown value is a startup error, not ``none``."""

    NONE = "none"
    EMBEDDED = "embedded"
    REMOTE = "remote"


def _safe_id(value: str) -> str:
    return value if _SAFE_ID.fullmatch(value or "") else "unknown"


def _principal(caller: FleetCaller) -> schemas.PrincipalCheck:
    """The PDP principal: identity and authority attributes, never token material."""
    agent_id = _safe_id(caller.client_id)
    return schemas.PrincipalCheck(
        uri=f"agent:{agent_id}",
        attributes={
            "agent_id": agent_id,
            "user_id": _safe_id(caller.subject),
            "tenant": caller.tenant,
            "scopes": sorted(caller.capabilities),
            "groups": list(caller.groups),
            "transport": caller.transport,
            "jwt_verified": caller.transport == "http",
        },
    )


def _tool_resource(name: str) -> schemas.ResourceCheck:
    """One canonical resource per tool, identical for list and call."""
    uri = f"mcp:tools:{name}"
    return schemas.ResourceCheck(
        uri=uri, attributes={"component_type": "tools", "name": name, "uri": uri}
    )


class ServedPolicyFilter(Middleware):
    """Narrow ``tools/list`` and ``tools/call`` to what the policy allows."""

    def __init__(self, decision_point: PolicyDecisionPoint) -> None:
        self._pdp = decision_point

    @staticmethod
    def _caller_principal() -> schemas.PrincipalCheck:
        caller = resolve_fleet_caller()
        if caller is None:
            raise ToolError("Access denied: no verified caller (POLICY_DENIED)")
        return _principal(caller)

    async def _decisions(
        self, names: Sequence[str]
    ) -> list[schemas.CheckResponse | None]:
        """One decision per tool: the first denial across both actions, else allow.

        ``None`` means allowed; a misaligned PDP answer denies every tool.
        """
        principal = self._caller_principal()
        requests = [
            schemas.CheckRequest(
                principal=principal, resource=_tool_resource(name), action=action
            )
            for name in names
            for action in TOOL_ACTIONS
        ]
        responses = await self._pdp.bulk_check(requests) if requests else []
        if len(responses) != len(requests):
            denied = schemas.CheckResponse(allowed=False, reason="misaligned")
            return [denied for _ in names]
        width = len(TOOL_ACTIONS)
        grouped = [responses[i : i + width] for i in range(0, len(responses), width)]
        return [next((r for r in group if not r.allowed), None) for group in grouped]

    async def allowed_tools(self, names: Sequence[str]) -> list[bool]:
        """Whether each named tool is allowed for the current caller."""
        try:
            decisions = await self._decisions(names)
        except ToolError:
            return [False for _ in names]
        return [decision is None for decision in decisions]

    async def authorize_tool(self, name: str) -> None:
        """Refuse a call the listing would not show."""
        (decision,) = await self._decisions([name])
        if decision is None:
            return
        if decision.reason == UNAVAILABLE_REASON:
            raise ToolError("Policy service unavailable (POLICY_UNAVAILABLE)")
        raise ToolError(f"Access denied: tool '{name}' (POLICY_DENIED)")

    async def on_list_tools(self, context: Any, call_next: Any) -> Any:
        tools = list(await call_next(context))
        allowed = await self.allowed_tools([tool.name for tool in tools])
        return [tool for tool, ok in zip(tools, allowed, strict=True) if ok]

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        await self.authorize_tool(str(getattr(context.message, "name", "")))
        return await call_next(context)


def build_policy_filter(config: Any) -> ServedPolicyFilter | None:
    """Build the filter for ``EUNOMIA_TYPE``; ``None`` only for ``none``.

    Raises when the mode is unknown or the policy source cannot be loaded, so
    a misconfigured Eunomia stops the server instead of serving unfiltered.
    """
    mode = EunomiaMode(str(getattr(config, "eunomia_type", "none") or "none").lower())
    if mode is EunomiaMode.NONE:
        return None
    if mode is EunomiaMode.REMOTE:
        remote_url = getattr(config, "eunomia_remote_url", None)
        return ServedPolicyFilter(RemotePolicy(remote_url, config))
    policy_file = getattr(config, "eunomia_policy_file", None) or "mcp_policies.json"
    return ServedPolicyFilter(EmbeddedPolicy([load_policy_file(policy_file)]))


_ACTIVE: list[ServedPolicyFilter] = []


def install_policy_filter(config: Any) -> ServedPolicyFilter | None:
    """Build the filter and make it the one :func:`authorize_native_call` uses."""
    policy = build_policy_filter(config)
    _ACTIVE.clear()
    if policy is not None:
        _ACTIVE.append(policy)
    return policy


def served_policy_middlewares(config: Any) -> list[ServedPolicyFilter]:
    """The policy middleware to install on the served MCP surface (none when off)."""
    policy = install_policy_filter(config)
    return [policy] if policy is not None else []


async def authorize_native_call(tool_name: str) -> None:
    """Apply the installed policy to a native call outside the MCP middleware."""
    for policy in _ACTIVE:
        await policy.authorize_tool(tool_name)
