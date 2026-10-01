"""Digest-addressable GraphOS operation schema resources."""

from __future__ import annotations

import json
from typing import Any


async def registry_index(
    registry: Any, caller: Any, policy_gate: Any
) -> dict[str, Any]:
    """Return only operation IDs discoverable by this caller."""

    from graph_os.api.mcp.discovery import visible_ops

    return {
        "api_version": registry.api_version,
        "registry_digest": registry.digest,
        "ops": sorted(op.id for op in await visible_ops(registry, caller, policy_gate)),
    }


async def operation_spec(
    registry: Any, op_id: str, caller: Any, policy_gate: Any
) -> dict[str, Any]:
    """Return a full spec only when visible to this verified caller."""

    from graph_os.api.mcp.discovery import visible_ops
    from graph_os.api.registry.digest import canonical_op

    op = registry.get(op_id)
    if op is None or op not in await visible_ops(registry, caller, policy_gate):
        raise ValueError("Unknown GraphOS operation")
    return {
        "api_version": registry.api_version,
        "registry_digest": registry.digest,
        "spec": canonical_op(op),
    }


def register_resources(
    mcp: Any, registry: Any, caller_for_request: Any, policy_gate: Any
) -> None:
    """Register the two schema resources for the server cutover lane."""

    @mcp.resource("graphos://registry", mime_type="application/json")
    async def _registry() -> str:
        return json.dumps(
            await registry_index(registry, caller_for_request(), policy_gate),
            sort_keys=True,
        )

    @mcp.resource("graphos://ops/{op_id}", mime_type="application/json")
    async def _operation(op_id: str) -> str:
        return json.dumps(
            await operation_spec(registry, op_id, caller_for_request(), policy_gate),
            sort_keys=True,
        )
