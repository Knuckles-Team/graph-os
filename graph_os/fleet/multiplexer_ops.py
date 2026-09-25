"""Registry-facing dynamic fleet operations; MCP wiring is a separate cutover."""

from __future__ import annotations

import builtins
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, cast

from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog, ItemKind
from graph_os.fleet.session_loads import SessionLoads

Loadable = Callable[[CatalogItem, Any], Awaitable[bool]]
Mount = Callable[[CatalogItem, Callable[..., Awaitable[Any]] | None], Awaitable[None]]
Notify = Callable[[str], Awaitable[bool]]
Invoke = Callable[[str, Mapping[str, Any], Any, str], Awaitable[Any]]
Health = Callable[[], Mapping[str, Any]]
SessionKeyFor = Callable[[Any], str | None]
NativeName = Callable[[CatalogItem], str]
ReadItem = Callable[[CatalogItem, Mapping[str, Any], Any], Awaitable[Any]]


class MultiplexerOps:
    """Discover, mount and track items under injected caller authority.

    The native tool body is only an ``invoke('fleet.call')`` wrapper. The final
    MCP cutover owns registration and session-visibility middleware; it must
    use :meth:`dispatchable` for both tools/list and tools/call.
    """

    def __init__(
        self,
        *,
        catalog: FleetCatalog,
        sessions: SessionLoads,
        loadable: Loadable,
        mount: Mount,
        notify: Notify,
        invoke: Invoke,
        health: Health,
        session_key_for: SessionKeyFor | None = None,
        callable_item: Loadable | None = None,
        native_name: NativeName | None = None,
        read_item: ReadItem | None = None,
    ) -> None:
        self.catalog = catalog
        self.sessions = sessions
        self._loadable = loadable
        self._mount = mount
        self._notify = notify
        self._invoke = invoke
        self._health = health
        self._session_key_for = session_key_for or (lambda _caller: None)
        self._callable_item = callable_item

        self._native_name = native_name or (lambda item: item.id)
        self._native_ids: dict[str, str] = {}
        self._read_item = read_item

    async def admitted_tool(self, caller: Any, server: str, tool: str) -> CatalogItem:
        """Return one call-authorized descriptor without requiring discovery scope."""
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        item = await self.catalog._raw_get(f"fleet:tool:{server}/{tool}")
        if (
            item is None
            or item.kind != "tool"
            or item.server != server
            or item.name != tool
        ):
            raise PermissionError("fleet tool is unavailable")
        if not item.required_scopes.issubset(caller.effective_scopes):
            raise PermissionError("child scopes are required")
        if (
            self._callable_item is None
            or await self._callable_item(item, caller) is not True
        ):
            raise PermissionError("fleet call denied by policy")
        return item

    def _session_key(self, caller: Any) -> str:
        key = self._session_key_for(caller)
        if not isinstance(key, str) or not key:
            raise PermissionError("verified MCP session is required")
        return key

    async def search(self, caller: Any, **params: Any) -> dict[str, Any]:
        """Registry service contract for ``fleet.catalog.search``."""
        if "mcp:discover" not in caller.effective_scopes:
            raise PermissionError("mcp:discover is required")
        return await self.find_tools(caller, **params)

    async def list(self, caller: Any, **params: Any) -> dict[str, Any]:
        """Registry browse projection over the same filtered catalog."""
        return await self.search(caller, **{**params, "browse": True})

    async def load(
        self,
        caller: Any,
        items: Iterable[str],
        auto_unload: bool = False,
        evict: str | None = None,
    ) -> dict[str, Any]:
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        return await self.load_tools(
            caller,
            self._session_key(caller),
            items,
            auto_unload=auto_unload,
            evict=evict,
        )

    async def unload(
        self,
        caller: Any,
        items: Iterable[str] = (),
        servers: Iterable[str] = (),
        kinds: Iterable[ItemKind] = (),
        all_items: bool = False,
    ) -> dict[str, Any]:
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        return await self.unload_tools(
            caller,
            self._session_key(caller),
            items=items,
            servers=servers,
            kinds=kinds,
            all_items=all_items,
        )

    async def status(self, caller: Any, servers: Iterable[str] = ()) -> dict[str, Any]:
        if "mcp:discover" not in caller.effective_scopes:
            raise PermissionError("mcp:discover is required")
        key = self._session_key_for(caller)
        snapshot = (
            self.multiplexer_status(key)
            if key
            else {
                "children": dict(self._health()),
                "session": None,
            }
        )
        selected = set(servers)
        if selected:
            children = cast(Mapping[str, Any], snapshot["children"])
            snapshot["children"] = {
                name: state for name, state in children.items() if name in selected
            }
        return snapshot

    async def find_tools(
        self,
        caller: Any,
        *,
        query: str = "",
        kinds: Iterable[ItemKind] = (),
        servers: Iterable[str] = (),
        browse: bool = False,
        cursor: str | None = None,
        limit: int = 20,
        context_budget_tokens: int | None = None,
    ) -> dict[str, Any]:
        return await self.catalog.search(
            caller,
            query=query,
            kinds=kinds,
            servers=servers,
            browse=browse,
            cursor=cursor,
            limit=limit,
            context_budget_tokens=context_budget_tokens,
        )

    def _forwarder(self, item: CatalogItem) -> Callable[..., Awaitable[Any]]:
        if item.server is None:
            raise ValueError("tool has no server")

        async def call(arguments: Mapping[str, Any], caller: Any) -> Any:
            return await self._invoke(
                "fleet.call",
                {
                    "server": item.server,
                    "tool": item.name,
                    "arguments": dict(arguments),
                },
                caller,
                "mcp",
            )

        return call

    def _loaded_result(self, item: CatalogItem) -> dict[str, Any]:
        result = item.public()
        if item.kind == "tool":
            result.update(
                {
                    "callable_as": self._native_name(item),
                    "input_schema": dict(item.schema),
                    "fallback": {
                        "verb": "act",
                        "op": "fleet.call",
                        "params": {
                            "server": item.server,
                            "tool": item.name,
                            "arguments": {},
                        },
                    },
                }
            )
        elif item.kind == "prompt":
            result["prompt_name"] = self._native_name(item)
            if item.body is not None:
                result["body"] = item.body
        elif item.kind == "resource":
            result["uri"] = self._native_name(item)
        elif item.kind == "skill":
            result["body"] = item.body
        return result

    async def load_tools(
        self,
        caller: Any,
        session_key: str,
        items: Iterable[str],
        *,
        auto_unload: bool = False,
        evict: str | None = None,
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(items))
        if not ids or len(ids) > 256:
            raise ValueError("load expects 1..256 item ids")
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        resolved: builtins.list[CatalogItem] = []
        for item_id in ids:
            item = await self.catalog.get(item_id, caller)
            if item is None or not await self._loadable(item, caller):
                raise PermissionError(f"item is unavailable for load: {item_id}")
            resolved.append(item)
        if (
            len(set(ids) | self.sessions.loaded(session_key)) > self.sessions.cap
            and evict != "lru"
        ):
            # The state machine provides the detailed LOAD_CAP_EXCEEDED payload.
            self.sessions.load(session_key, ids, evict=evict)
        if len(ids) > self.sessions.cap:
            raise ValueError("request exceeds session cap")
        for item in resolved:
            if item.kind == "connector_item":
                continue
            if item.kind in {"tool", "prompt", "resource", "resource_template"}:
                name = self._native_name(item)
                prior = self._native_ids.get(name)
                if prior is not None and prior != item.id:
                    raise ValueError(f"native fleet name collision: {name}")
                self._native_ids[name] = item.id
            if item.kind == "skill":
                # A skill body is returned in the load result until a native
                # Skills-over-MCP provider is bound for this client profile.
                continue
            if (
                item.kind in {"resource", "resource_template"}
                and self._read_item is None
            ):
                raise RuntimeError("governed fleet read adapter is unavailable")
            await self._mount(
                item, self._forwarder(item) if item.kind == "tool" else None
            )
        state = self.sessions.load(
            session_key, ids, evict=evict, auto_unload=auto_unload
        )
        changed = bool(state["evicted"]) or bool(ids)
        sent = await self._notify(session_key) if changed else True
        self.sessions.notification(session_key, sent)
        return {
            "items": [self._loaded_result(item) for item in resolved],
            **state,
            "notification_sent": sent,
        }

    async def unload_tools(
        self,
        caller: Any,
        session_key: str,
        *,
        items: Iterable[str] = (),
        servers: Iterable[str] = (),
        kinds: Iterable[ItemKind] = (),
        all_items: bool = False,
    ) -> dict[str, Any]:
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        loaded = self.sessions.loaded(session_key)
        targets = set(items) & loaded
        server_filter, kind_filter = set(servers), set(kinds)
        if all_items:
            targets.update(loaded)
        elif server_filter or kind_filter:
            for item_id in loaded:
                item = await self.catalog.get(item_id, caller)
                if item is not None and (
                    item.server in server_filter or item.kind in kind_filter
                ):
                    targets.add(item_id)
        removed = self.sessions.unload(session_key, targets)
        sent = await self._notify(session_key) if removed else True
        self.sessions.notification(session_key, sent)
        return {
            "unloaded": removed,
            "notification_sent": sent,
            "session_total": len(self.sessions.loaded(session_key)),
        }

    async def redeliver_pending(self, session_key: str) -> bool:
        """Resend on the next request for clients that missed list_changed."""
        if not self.sessions.status(session_key)["list_changed_pending"]:
            return True
        sent = await self._notify(session_key)
        self.sessions.redelivered(session_key, sent)
        return sent

    async def revoke_invisible(
        self, caller: Any, session_key: str
    ) -> builtins.list[str]:
        """Drop loaded items whose discovery/load policy changed."""
        removed: builtins.list[str] = []
        for item_id in self.sessions.loaded(session_key):
            item = await self.catalog.get(item_id, caller)
            if item is None or not await self._loadable(item, caller):
                removed.append(item_id)
        if removed:
            self.sessions.unload(session_key, removed)
            self.sessions.notification(session_key, await self._notify(session_key))
        return sorted(removed)

    async def invalidate_policy_revision(
        self, _revision: str
    ) -> dict[str, builtins.list[str]]:
        """Drop every loaded grant when the PDP revision changes.

        A revision callback has no request caller and cannot safely re-evaluate
        another session's principal. That session may explicitly load again
        under its new policy on its next request.
        """
        removed_by_session: dict[str, builtins.list[str]] = {}
        for key in self.sessions.active_keys():
            removed = self.sessions.unload(key, self.sessions.loaded(key))
            if not removed:
                continue
            removed_by_session[key] = removed
        for key in removed_by_session:
            sent = await self._notify(key)
            self.sessions.notification(key, sent)
        return removed_by_session

    def dispatchable(self, session_key: str, item_id: str) -> bool:
        return self._native_ids.get(item_id) in self.sessions.loaded(session_key)

    def catalog_id_for_native(self, name: str) -> str | None:
        return self._native_ids.get(name)

    async def read_loaded_item(
        self,
        caller: Any,
        session_key: str,
        item_id: str,
        params: Mapping[str, Any],
    ) -> Any:
        """Render one loaded non-tool item under fresh caller policy."""
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        if item_id not in self.sessions.loaded(session_key):
            raise PermissionError("item is not loaded in this session")
        item = await self.catalog.get(item_id, caller)
        if item is None or not await self._loadable(item, caller):
            raise PermissionError("item is unavailable")
        if item.kind in {"prompt", "skill"} and item.body is not None:
            return item.body
        if item.kind not in {"prompt", "resource", "resource_template"}:
            raise ValueError("item has no read surface")
        if self._read_item is None:
            raise RuntimeError("governed fleet read adapter is unavailable")
        return await self._read_item(item, params, caller)

    def multiplexer_status(self, session_key: str) -> dict[str, Any]:
        return {
            "children": dict(self._health()),
            "session": self.sessions.status(session_key),
        }
