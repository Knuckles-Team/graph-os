"""Production GraphOS launcher with one verified process authority bundle."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any


def main() -> None:
    """Preparse transport, mint once, and serve the governed API and fleet."""
    from agent_connector_sdk.artifacts.catalog import VerifiedPackCatalogReader
    from agent_connector_sdk.mcp.parser import create_mcp_parser
    from agent_utilities.core.config import config, load_config
    from agent_utilities.security.request_identity import (
        local_process_authority_enabled,
    )

    from graph_os.api.host_bootstrap import compose_process_host_inputs
    from graph_os.fleet.catalog_reader import DeferredFleetCatalogReader
    from graph_os.mcp_server import runtime

    load_config()
    parser = create_mcp_parser(transport_choices=("stdio", "streamable-http"))
    args, _ = parser.parse_known_args()
    if args.help:
        parser.print_help(sys.stderr)
        return
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")

    session = runtime._mint_process_session(args.transport)
    claims = session.engine_verified_context()
    tenant = claims.get("tenant")
    if claims.get("principal") != "svc:graph-os" or not isinstance(tenant, str):
        raise PermissionError("verified graph-os process authority is required")
    client = runtime.graph_client(tenant)
    if not callable(getattr(client, "use_verified_context", None)):
        raise RuntimeError("verified EG client is unavailable")
    pack_reader = VerifiedPackCatalogReader(client, tenant_id=tenant)

    async def sdk_entries() -> tuple[Mapping[str, Any], ...]:
        current = session.engine_verified_context()
        if current != claims:
            raise PermissionError("served pack catalog authority changed")
        with client.use_verified_context(current):
            rows = await pack_reader.sdk_entries()
        if any(row.get("tenant_id") != tenant for row in rows):
            raise PermissionError("served pack catalog crossed tenant authority")
        return rows

    local = args.transport == "stdio" and local_process_authority_enabled(config)
    inputs = compose_process_host_inputs(
        session,
        identity_mode="local" if local else "external",
        transport=args.transport,
        fleet_reader=DeferredFleetCatalogReader(),
        sdk_entries=sdk_entries,
        bindings={},
    )
    from graph_os.mcp_server.server import mcp_server

    mcp_server(host_runtime_inputs=inputs)


if __name__ == "__main__":
    main()
