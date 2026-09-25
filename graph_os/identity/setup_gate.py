"""First-run setup: every fresh production instance forces an administrator.

Operator ruling 2026-09-24: registration is administrator-only, and a fresh
instance cannot be used until its first administrator exists. The tiny
profile seeds ``none`` mode at boot (the bootstrap principal is that
administrator, claimed later with ``graph-os-identity claim``); every other
profile starts UNINITIALIZED and the first-run form creates the administrator
through the engine's ``initialize`` op.

The form is not first-come-first-served: it requires a one-time setup code
that only the operator can read (``GRAPHOS_SETUP_CODE``, or a code generated
at start and written to the process log), so an exposed but unconfigured
instance cannot be claimed by whoever reaches it first.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from .admission import AdmissionService

__all__ = ["SetupGate", "seed_first_boot"]

logger = logging.getLogger(__name__)


class SetupGate:
    """Holds the one-time first-run setup code."""

    def __init__(self, code: str | None = None) -> None:
        self._code = (code or "").strip() or secrets.token_urlsafe(18)
        self._announced = bool((code or "").strip())

    def announce(self) -> None:
        """Log the generated code once (the operator reads the process log)."""
        if self._announced:
            return
        self._announced = True
        logger.warning(
            "GraphOS identity is not initialized. Create the first administrator "
            "at /auth/setup with setup code: %s",
            self._code,
        )

    def accepts(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self._code.encode())


async def seed_first_boot(admission: AdmissionService, mode: str) -> str | None:
    """Initialize the store on first boot when the profile's mode needs no admin.

    ``none`` is seeded at once (the bootstrap principal is the administrator);
    ``local`` waits for the setup form. Answers the stored mode, or ``None``
    while setup is still owed.
    """
    stored = await admission.mode()
    if stored is not None or mode != "none":
        return stored
    await admission.broker.initialize("none")
    return "none"
