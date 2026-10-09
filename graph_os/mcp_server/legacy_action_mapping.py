"""Typed action-to-operation mapping for retired legacy MCP host actions.

RF-ADR-009 host-composition-boundary R005 requires that every MCP action
the agent runtime's legacy MCP host used to serve is, once that host is
retired, served by ``graph_os.mcp_server.runtime`` against an *approved*
action-to-operation mapping -- not an ad hoc or implicit one. This module
gives that mapping a typed shape and a validator that refuses an action
with no approved mapping and refuses a mapping table with the same action
listed twice (an ambiguous mapping is as unsafe as a missing one).

Parity between this approved table and what ``runtime.py`` actually
serves is a further (``.2``+) slice; this slice is the typed model, the
construction-time duplicate refusal, and the lookup-time unmapped
refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


class DuplicateActionMappingError(ValueError):
    """Raised when the same legacy MCP action appears more than once in a table."""


class UnmappedActionError(ValueError):
    """Raised when a served action has no approved operation mapping."""


@dataclass(frozen=True)
class ActionOperationMapping:
    """One legacy MCP action's approved mapping onto a runtime operation."""

    action: str
    operation: str


def build_approved_action_mapping(
    entries: tuple[ActionOperationMapping, ...],
) -> MappingProxyType[str, str]:
    """Build an action->operation lookup table, refusing duplicate actions."""

    table: dict[str, str] = {}
    for entry in entries:
        if entry.action in table:
            raise DuplicateActionMappingError(
                f"action {entry.action!r} is mapped more than once "
                f"(already -> {table[entry.action]!r}, again -> {entry.operation!r})"
            )
        table[entry.action] = entry.operation
    return MappingProxyType(table)


def validate_served_actions(
    served_actions: tuple[str, ...],
    approved: MappingProxyType[str, str],
) -> None:
    """Refuse any served action absent from the approved mapping table."""

    unmapped = sorted({action for action in served_actions if action not in approved})
    if unmapped:
        raise UnmappedActionError(
            f"actions have no approved action-to-operation mapping: {unmapped}"
        )


#: The approved mapping for a representative slice of actions
#: ``graph_os.mcp_server.runtime`` serves, each traced to the legacy MCP
#: host action it replaces. Extending this to full parity is a later slice.
APPROVED_ACTION_MAPPING = build_approved_action_mapping(
    (
        ActionOperationMapping("code_context", "graph_code.code_context"),
        ActionOperationMapping("explain", "graph_code.explain"),
        ActionOperationMapping("recall_memory", "graph_query.recall_memory"),
        ActionOperationMapping("submit_sdd", "graph_orchestrate.submit_sdd"),
        ActionOperationMapping(
            "ingest_knowledge_pack", "source_sync.ingest_knowledge_pack"
        ),
        ActionOperationMapping("process_writeback", "source_sync.process_writeback"),
        ActionOperationMapping("set_secret", "secret_vault.set_secret"),
        ActionOperationMapping("vault_sync", "secret_vault.vault_sync"),
        ActionOperationMapping("install_hooks", "graph_loops.install_hooks"),
        ActionOperationMapping("uninstall_hooks", "graph_loops.uninstall_hooks"),
    )
)
