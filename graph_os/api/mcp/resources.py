"""Digest-addressable GraphOS operation schema resources."""

from __future__ import annotations

import json
from typing import Any


def registry_index(registry: Any, caller: Any, policy: Any) -> dict[str, Any]:
    """Return only operation IDs discoverable by this caller."""

    from graph_os.api.registry import Surface

    return {
        "api_version": registry.api_version,
        "registry_digest": registry.digest,
        "ops": sorted(
            op.id for op in registry.find(caller, policy=policy, surface=Surface.MCP)
        ),
    }


def operation_spec(
    registry: Any, op_id: str, caller: Any, policy: Any
) -> dict[str, Any]:
    """Return a full spec only when visible to this verified caller."""

    from graph_os.api.registry import Surface, authorized
    from graph_os.api.registry.digest import canonical_op

    op = registry.get(op_id)
    if (
        op is None
        or Surface.MCP not in op.surfaces
        or not authorized(op, caller, policy=policy)
    ):
        raise ValueError("Unknown GraphOS operation")
    return {
        "api_version": registry.api_version,
        "registry_digest": registry.digest,
        "spec": canonical_op(op),
    }


def register_resources(
    mcp: Any, registry: Any, caller_for_request: Any, policy: Any
) -> None:
    """Register the two schema resources for the server cutover lane."""

    @mcp.resource("graphos://registry", mime_type="application/json")
    def _registry() -> str:
        return json.dumps(
            registry_index(registry, caller_for_request(), policy), sort_keys=True
        )

    @mcp.resource("graphos://ops/{op_id}", mime_type="application/json")
    def _operation(op_id: str) -> str:
        return json.dumps(
            operation_spec(registry, op_id, caller_for_request(), policy),
            sort_keys=True,
        )
