"""Caller-bound authority boundary for native fleet prompt/resource reads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from graph_os.fleet.catalog_items import CatalogItem

CredentialMode = Callable[[CatalogItem, Any], Awaitable[str]]
PolicyCheck = Callable[[CatalogItem, Any], Awaitable[bool]]
DelegatedRead = Callable[[CatalogItem, Mapping[str, Any], Any], Awaitable[Any]]


class NativeReadGateway:
    """Require trusted delegation before any child prompt/resource dispatch.

    The dispatcher must attach the caller's delegated credential to the child
    request. A service-only child has no native read route here. Static EG
    prompt/skill bodies are handled by ``MultiplexerOps`` after the same item
    visibility and load checks, without contacting a child.
    """

    def __init__(
        self,
        *,
        credential_mode: CredentialMode,
        policy_check: PolicyCheck,
        delegated_read: DelegatedRead,
    ) -> None:
        self._credential_mode = credential_mode
        self._policy_check = policy_check
        self._delegated_read = delegated_read

    async def read(
        self, item: CatalogItem, params: Mapping[str, Any], caller: Any
    ) -> Any:
        if item.kind not in {"prompt", "resource", "resource_template"}:
            raise ValueError("item has no native read surface")
        if item.server is None:
            raise ValueError("native item has no admitted server")
        scopes = caller.effective_scopes
        if "mcp:delegate" not in scopes or not item.required_scopes.issubset(scopes):
            raise PermissionError("native item scopes are required")
        if await self._credential_mode(item, caller) != "delegated":
            raise PermissionError("child has no caller-delegated credential route")
        if await self._policy_check(item, caller) is not True:
            raise PermissionError("native item denied by policy")
        return await self._delegated_read(item, params, caller)
