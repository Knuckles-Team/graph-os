"""Shared response shapes of the operator console's decision routes.

Every console decision (elevation approval, live-order approval and denial)
answers with the same no-store JSON: a stable refusal code, or an upstream
failure through the public error surface.
"""

from __future__ import annotations

import logging

from agent_utilities.security.error_surface import public_error_payload
from starlette.responses import JSONResponse

__all__ = ["NO_STORE", "refusal", "upstream_failure"]

NO_STORE = {"Cache-Control": "no-store"}


def refusal(code: str, status: int) -> JSONResponse:
    """A refused decision: a stable code, no detail."""
    return JSONResponse(
        {"status": "error", "code": code}, status_code=status, headers=NO_STORE
    )


def upstream_failure(exc: Exception, logger: logging.Logger) -> JSONResponse:
    """An unexpected failure behind the route, through the public error surface."""
    return JSONResponse(
        public_error_payload(exc, logger=logger), status_code=502, headers=NO_STORE
    )
