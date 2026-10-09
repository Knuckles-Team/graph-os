"""GraphOS's own security boundary modules (GRAPHOS-HOST-R008).

Hosts the security-policy-middleware module moved from the agent runtime, plus
the authorization conformance surfaces (AU-BOUNDARY-R014's security/* seed
group). Further boundary modules (request identity, error surface, browser
auth) land here incrementally, one GRAPHOS-HOST-R008 slice at a time.
"""

from __future__ import annotations

from .security_policy_middleware import SecurityPolicyMiddleware, SecurityViolation

__all__ = ["SecurityPolicyMiddleware", "SecurityViolation"]
