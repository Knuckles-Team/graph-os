"""Who may ask, and who executes (coordinator ruling 2026-09-24).

People hold only narrow finance DOMAIN scopes; they never hold the
infrastructure scopes the work needs. graph-os is the confused-deputy-safe
executor:

1. :func:`require_domain_scope` -- the caller's verified session holds the
   exact domain scope of the action (``finance:alerts``, ``finance:track``,
   ``finance:backfill`` or ``finance:propose-order``);
2. :func:`require_reader` -- EG says the caller's own request to READ the
   tenant graph would be admitted now (``CheckAccess``), so graph-os never
   acts on data the caller could not read itself;
3. only then does the work run on the graph-os service client (the process
   identity's ``compute:finance``, ``timeseries:*``, ``broker:*``), and every
   record it creates names the verified caller as its owner.

Alert delivery repeats (2) for the subscriber at every event, so a revoked
grant stops alerts at the next one.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

__all__ = [
    "ACTION_SCOPES",
    "FinanceUnavailable",
    "FinanceService",
    "can_read",
    "finance_service",
    "install_finance_service",
    "require_domain_scope",
    "require_reader",
]

#: The exact domain scope each ``graph_finance`` action needs.
ACTION_SCOPES: dict[str, str] = {
    "subscribe": "finance:alerts",
    "unsubscribe": "finance:alerts",
    "subscriptions": "finance:alerts",
    "alerts": "finance:alerts",
    "explain_flip": "finance:alerts",
    "scan": "finance:alerts",
    "track": "finance:track",
    "untrack": "finance:track",
    "tracked": "finance:track",
    "backfill": "finance:backfill",
    "propose_order": "finance:propose-order",
    "order_status": "finance:propose-order",
}


class FinanceUnavailable(RuntimeError):
    """The graph-os finance executor is not composed in this process."""


def require_domain_scope(scopes: frozenset[str], action: str) -> None:
    """Refuse unless the caller's session holds the action's exact scope."""
    scope = ACTION_SCOPES[action]
    if scope not in scopes:
        raise PermissionError(f"FINANCE_SCOPE_REQUIRED: {action} needs {scope}")


async def can_read(service: Any, agent_id: str) -> bool:
    """EG's answer: would ``agent_id``'s own read of the tenant graph be admitted?"""
    if not agent_id:
        return False
    return bool(await service.consensus.check_access(agent_id, "read"))


async def require_reader(service: Any, agent_id: str) -> None:
    """Refuse unless the caller could read the tenant graph itself."""
    if not await can_read(service, agent_id):
        raise PermissionError("FINANCE_READ_AUTHORITY_REQUIRED")


class FinanceService:
    """The graph-os service client the finance work executes on."""

    def __init__(
        self, authority: Callable[[], contextlib.AbstractContextManager[Any]]
    ) -> None:
        self._authority = authority

    @contextlib.contextmanager
    def client(self) -> Iterator[Any]:
        with self._authority() as client:
            yield client


_SERVICE: FinanceService | None = None


def install_finance_service(service: FinanceService | None) -> None:
    """Compose (or, with ``None``, remove) the process's finance executor."""
    global _SERVICE
    _SERVICE = service


def finance_service() -> FinanceService:
    if _SERVICE is None:
        raise FinanceUnavailable("the finance executor is not composed")
    return _SERVICE
