"""Tenant-scoped market read service (GRAPHOS-DATA-MARKET-R003, FI-01/FI-02).

Server-side market read logic consumed by the not-yet-registered
``graph_os/api/ops/finance.py`` operation (deferred for the same reason
``graph_os/api/ops/access.py`` defers its ``approvals.*`` operations: the
installed EG contract does not yet publish a ``finance:read`` scope for
``get_registry()`` to bind, tracked as GDM-06 follow-up in
``specs/data-and-market-projections/tasks.md``).

Mirrors ``graph_os.gateway.schema_context_service``'s fail-closed shape:
``get_market_read`` refuses with ``UNAVAILABLE`` *before* any provider
dispatch when the caller's tenant/account binding is missing or does not
match the requested scope (FI-02's "zero provider calls" proof). Paper and
live order submission (FI-08/FI-10) are separate, not-yet-built slices.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal

__all__ = [
    "AccountBinding",
    "MarketProvider",
    "MarketReadRequest",
    "MarketReadResult",
    "get_market_read",
]


class MarketReadRequest(BaseModel):
    """One tenant-scoped market read, as resolved by the caller's session."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=256)
    account_id: str = Field(min_length=1, max_length=256)
    symbol: str = Field(min_length=1, max_length=64)


class MarketReadResult(BaseModel):
    """FI-01: exact decimal price string, source, as-of, currency, staleness."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    account_id: str
    symbol: str
    price: str = Field(description="Exact decimal price, as a string.")
    currency: str
    source: str
    as_of: str = Field(description="ISO-8601 timestamp this read is as of.")
    staleness_seconds: int = Field(ge=0)


class AccountBinding(BaseModel):
    """A verified tenant/account pair.

    Resolving this binding (session, grant, and provider account lookup) is
    the caller's responsibility; this module only refuses to dispatch when
    it is absent or does not match the request.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=256)
    account_id: str = Field(min_length=1, max_length=256)


class MarketProvider(Protocol):
    """The one capability this service dispatches through."""

    def market_read(self, request: MarketReadRequest) -> MarketReadResult:
        """Return a live market read for an already-authorized request."""


def _binding_matches(
    binding: AccountBinding | None, request: MarketReadRequest
) -> bool:
    return (
        binding is not None
        and binding.tenant_id == request.tenant_id
        and binding.account_id == request.account_id
    )


def get_market_read(
    provider: Any,
    request: MarketReadRequest,
    *,
    account_binding: AccountBinding | None,
) -> MarketReadResult:
    """Answer one market read, or refuse before any provider dispatch.

    FI-02: a missing or mismatched ``account_binding`` returns typed
    ``UNAVAILABLE`` and never calls ``provider`` at all -- not even to
    check whether it has the capability.
    """

    if not _binding_matches(account_binding, request):
        raise GraphOSRefusal(
            GraphOSErrorCode.UNAVAILABLE,
            "unverified tenant/account binding for market read",
            details={"tenant_id": request.tenant_id, "symbol": request.symbol},
        )
    read = getattr(provider, "market_read", None)
    if read is None:
        raise GraphOSRefusal(
            GraphOSErrorCode.UNAVAILABLE,
            "no market read provider configured",
            details={"symbol": request.symbol},
        )
    return read(request)
