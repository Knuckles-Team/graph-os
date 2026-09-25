"""The operator console's live-order approval (EH-423).

``POST /finance/orders/approve`` and ``POST /finance/orders/deny`` are the
ONLY places GraphOS decides a live-order proposal. They are plain gateway
routes, not MCP tools, so no agent (chat, fleet child or A2A peer) can reach
them through a tool call -- ``graph_finance`` can propose and read, never
decide.

The decider is the caller's verified request session, never a body field. The
body names only WHICH proposal and the digest of the order the person was
shown. :mod:`graph_os.finance.orders` refuses a session without the exact
``finance:approve-live-order`` scope, a delegated session, a stale view and --
for an approval -- the proposer approving their own order; epistemic-graph
then refuses a change set whose actor is not the verified caller.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from graph_os.finance.orders import (
    OrderDecision,
    OrderRefused,
    approve_order,
    deny_order,
)
from graph_os.gateway.console import NO_STORE, refusal, upstream_failure

logger = logging.getLogger(__name__)

__all__ = ["finance_order_approve", "finance_order_deny", "mount_finance_order_routes"]

_Decide = Callable[..., Awaitable[dict[str, Any]]]


async def _decision(request: Request) -> OrderDecision | None:
    try:
        return OrderDecision.model_validate(await request.json())
    except (ValueError, ValidationError):
        return None


async def _decide(decide: _Decide, decision: OrderDecision) -> dict[str, Any]:
    from agent_utilities.api.session import resolve_session

    from graph_os.gateway.ports import gateway_application

    session = resolve_session(required_scope="kg:read")
    claims = session.engine_verified_context()
    client = gateway_application().graph_client(session.tenant)
    with client.use_verified_context(claims):
        return await decide(
            client,
            claims,
            frozenset(session.scopes),
            decision,
            time.time_ns() // 1_000_000,
        )


async def _route(request: Request, decide: _Decide) -> JSONResponse:
    decision = await _decision(request)
    if decision is None:
        return refusal("invalid_request", 400)
    try:
        body = await _decide(decide, decision)
    except OrderRefused as refused:
        return refusal(refused.code, 403)
    except PermissionError:
        return refusal("ORDER_ACCESS_DENIED", 403)
    except Exception as exc:
        return upstream_failure(exc, logger)
    return JSONResponse({"status": "success", "order": body}, headers=NO_STORE)


async def finance_order_approve(request: Request) -> JSONResponse:
    """Approve one exact live-order proposal as the signed-in person."""
    return await _route(request, approve_order)


async def finance_order_deny(request: Request) -> JSONResponse:
    """Deny one pending live-order proposal as the signed-in person."""
    return await _route(request, deny_order)


def mount_finance_order_routes(app: Any, prefix: str = "") -> None:
    """Mount the console decision routes onto a Starlette/FastAPI ``app``."""
    app.add_route(
        prefix + "/finance/orders/approve", finance_order_approve, methods=["POST"]
    )
    app.add_route(prefix + "/finance/orders/deny", finance_order_deny, methods=["POST"])
