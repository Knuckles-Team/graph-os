"""Process-local MCP list-change delivery state.

This module deliberately tracks only notifications owed to currently served
sessions.  Fleet identity, catalog generations, and reconciliation receipts
belong to epistemic-graph's RegisterServer, ConnectorPack, and AgentComponent
contracts and must never be reconstructed here.
"""

from __future__ import annotations


class SessionCatalogNotifications:
    """Bounded high-water marks for live-session ``tools/list_changed`` delivery."""

    def __init__(self) -> None:
        self._generation = 0
        self._pending: dict[str, int] = {}

    def queue(self, session_ids: list[str]) -> None:
        if not session_ids:
            return
        self._generation += 1
        for session_id in session_ids:
            self._pending[session_id] = self._generation

    def pending_generation(self, session_id: str) -> int | None:
        return self._pending.get(session_id)

    def acknowledge(self, session_id: str, generation: int) -> None:
        if self._pending.get(session_id) == generation:
            self._pending.pop(session_id, None)

    def has_pending(self, session_id: str) -> bool:
        return session_id in self._pending

    def clear(self) -> None:
        self._pending.clear()
