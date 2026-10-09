"""Shared fakes for tests exercising ``graph_os.epistemic`` adapters directly.

``Actor`` is the one fake structurally satisfying
``graph_os.epistemic.VerifiedActor`` that every direct-adapter test in this
tree authenticates as; duplicating the dataclass per module just lets the
fake drift from the real protocol it stands in for.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class Actor:
    """A fake structurally satisfying ``graph_os.epistemic.VerifiedActor``."""

    actor_id: str = "service:graph-os"
    tenant_id: str = "tenant:a"
    roles: Iterable[str] = ("graph-client",)
    authenticated: bool = True
    current: bool = True

    def ensure_credential_current(self) -> None:
        if not self.current:
            raise PermissionError("expired")
