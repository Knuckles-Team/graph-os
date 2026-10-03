"""Strict local claim preparation; no signing or authority provisioning.

The serving issuer must obtain a credential-derived Resolution and its source
expiry from the qualified EG owner before calling this pure mapping. Neither
the DTO nor the mapping result is an authentication capability.
"""

import secrets
from dataclasses import dataclass
from typing import Any

from .engine import IdentityUnavailable, Resolution, _text

ACCESS_TOKEN_MAX_SECONDS = 300


@dataclass(frozen=True, slots=True)
class IssuerSettings:
    issuer: str
    audience: str
    tenant: str
    access_ttl_seconds: int = ACCESS_TOKEN_MAX_SECONDS

    def __post_init__(self) -> None:
        for value in (self.issuer, self.audience, self.tenant):
            _text(value)
        ttl = self.access_ttl_seconds
        if type(ttl) is not int or not 1 <= ttl <= ACCESS_TOKEN_MAX_SECONDS:
            raise ValueError("access tokens live between 1 and 300 seconds")


def claims_for(
    resolution: Resolution,
    settings: IssuerSettings,
    *,
    source_expires_at_ms: int,
    now_ms: int,
) -> dict[str, Any]:
    """Prepare exact local claims; missing/expired source authority refuses.

    Call Resolution.narrow first when the qualified credential supplies an
    additional scope ceiling. The returned token claims and that resolution's
    context then carry the same effective scope set.
    """
    if not isinstance(resolution, Resolution):
        raise IdentityUnavailable("a strict resolution is required")
    for value in (source_expires_at_ms, now_ms):
        if type(value) is not int or value < 0:
            raise IdentityUnavailable("authoritative source expiry and clock required")
    context = resolution.request_context
    if context["tenant"] != settings.tenant or context["audience"] != settings.audience:
        raise IdentityUnavailable("resolution deployment binding disagrees")
    issued = now_ms // 1000
    expires = min(issued + settings.access_ttl_seconds, source_expires_at_ms // 1000)
    if expires * 1000 <= now_ms:
        raise PermissionError("source credential cannot support a live token")
    return {
        "iss": settings.issuer,
        "aud": context["audience"],
        "sub": resolution.principal_id,
        "tenant_id": context["tenant"],
        "principal_kind": resolution.kind,
        "roles": sorted(resolution.roles),
        "scope": " ".join(sorted(resolution.scopes)),
        "policy_version": context["policy_version"],
        "agent_id": context["agent_id"],
        "delegation": list(context["delegation"]),
        "iat": issued,
        "nbf": issued,
        "exp": expires,
        "jti": secrets.token_hex(16),
    }
