"""REST route for schema_context (GRAPHOS-DATA-MARKET-R005, GDM-03).

Mounts ``POST {prefix}/graph/schema/context`` and dispatches through the
same :func:`graph_os.gateway.schema_context_service.get_schema_context` a
future MCP ``graph_schema`` action will call (SC-05: the two surfaces must
never drift because they share one service, not because their response
shapes happen to match). The service still refuses every request with
``UNAVAILABLE`` until epistemic-graph publishes a ``schema_context`` client
(see that module's own docstring); this route makes that refusal — and,
once the capability exists, the answer — reachable over REST.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

SCHEMA_CONTEXT_PATH = "/graph/schema/context"

__all__ = ["SCHEMA_CONTEXT_PATH", "register_schema_context_routes"]


async def _schema_context_endpoint(request: Request) -> JSONResponse:
    from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal
    from graph_os.gateway.ports import gateway_application
    from graph_os.gateway.schema_context_service import get_schema_context
    from graph_os.gateway.schemas.schema_context import SchemaContextRequest

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "error", "message": "invalid JSON body"}, status_code=400
        )
    if not isinstance(body, dict):
        return JSONResponse(
            {"status": "error", "message": "object body required"}, status_code=400
        )
    try:
        parsed = SchemaContextRequest.model_validate(body)
    except ValidationError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

    try:
        result = get_schema_context(gateway_application().engine(), parsed)
    except GraphOSRefusal as exc:
        status = 503 if exc.code is GraphOSErrorCode.UNAVAILABLE else 400
        return JSONResponse(
            {"status": "error", "code": exc.code.value, "message": exc.message},
            status_code=status,
        )

    payload: Any = result.model_dump() if hasattr(result, "model_dump") else result
    return JSONResponse({"status": "success", "result": payload})


def register_schema_context_routes(app: Any, prefix: str = "/api") -> None:
    """Mount the schema-context REST route onto ``app`` (idempotent)."""

    path = f"{prefix}{SCHEMA_CONTEXT_PATH}"
    if any(
        getattr(route, "path", None) == path for route in getattr(app, "routes", [])
    ):
        return
    if hasattr(app, "add_api_route"):  # FastAPI
        app.add_api_route(
            path,
            _schema_context_endpoint,
            methods=["POST"],
            name="graph_schema_context",
            summary="Query schema context for a table or object.",
        )
    else:  # plain Starlette
        app.add_route(path, _schema_context_endpoint, methods=["POST"])
    logger.info("Mounted schema-context REST route at %s", path)
