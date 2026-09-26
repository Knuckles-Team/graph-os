"""A resolved principal's own verified graph session.

Administrator and self-service identity ops run under the CALLER's authority,
never GraphOS's: the engine checks the exact ``identity:admin`` /
``identity:self`` scope on the caller's own verified context. This module
turns a resolution into that context through the one public claims path —
the local issuer mints the token, the token is verified against the issuer's
own published keys, and agent-utilities projects the claims into its actor and
session exactly as it does for a bearer on any served request.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from .broker import IdentityBroker
from .engine import Resolution
from .issuer import TokenGrant

__all__ = ["session_for"]


def session_for(
    broker: IdentityBroker,
    resolution: Resolution,
    methods: Sequence[str],
    *,
    process_key: bool = False,
) -> Any:
    """The verified ``GraphSession`` of ``resolution`` (refuses a pending one)."""
    from agent_utilities.security.request_identity import (
        actor_from_claims,
        mint_graph_session,
    )

    if process_key:
        token = broker.issuer.mint_process(
            resolution, TokenGrant(tuple(methods), int(time.time()))
        )
        claims = broker.issuer.verify_process(token)
    else:
        token = broker.access_token(resolution, methods)
        claims = broker.issuer.verify(token)
    del token
    return mint_graph_session(actor_from_claims(claims))
