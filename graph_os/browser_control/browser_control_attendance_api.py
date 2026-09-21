"""Server-only contracts for the two-stage attended-arm handshake."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_utilities.knowledge_graph.core.session import GraphSession
from pydantic import BaseModel, ConfigDict, field_validator

from graph_os.browser_control.browser_control_common import (
    _DIGEST,
    _TRUST_EVIDENCE,
    RECENT_AUTH_GRANT_SECONDS,
    _identifier,
    _opaque_ref,
    _timestamps_are_finite,
    _trusted_issuer,
)


def _validate_recent_auth_references(grant: RecentAuthGrant) -> None:
    for value in (
        grant.grant_ref,
        grant.login_session_ref,
        grant.browser_session_ref,
        grant.principal_ref,
    ):
        _opaque_ref(value)
    required_prefixes = (
        (grant.grant_ref, "attended_", "recent-auth reference"),
        (grant.login_session_ref, "login_", "recent-auth login reference"),
        (grant.browser_session_ref, "browser_", "recent-auth browser reference"),
    )
    for value, prefix, label in required_prefixes:
        if not value.startswith(prefix):
            raise ValueError(f"{label} must be server verified")


@dataclass(frozen=True, slots=True)
class RecentAuthGrant:
    """Server-verified short-lived OIDC callback evidence before SPA reload."""

    session: GraphSession
    grant_ref: str
    login_session_ref: str
    browser_session_ref: str
    principal_ref: str
    origin: str
    route_id: str
    access_token_expires_at: float
    grant_issued_at: float
    grant_expires_at: float
    attended_auth_time: float
    attended_acr: str
    attended_issuer: str

    def __post_init__(self) -> None:
        _validate_recent_auth_references(self)
        _identifier(self.route_id)
        timestamps = (
            self.access_token_expires_at,
            self.grant_issued_at,
            self.grant_expires_at,
            self.attended_auth_time,
        )
        if not _timestamps_are_finite(timestamps):
            raise ValueError("recent-auth timestamps must be finite")
        if not self.attended_auth_time <= self.grant_issued_at < self.grant_expires_at:
            raise ValueError("recent-auth evidence is not chronological")
        if self.grant_expires_at > self.access_token_expires_at:
            raise ValueError("recent-auth grant cannot outlive the access token")
        if self.grant_expires_at - self.grant_issued_at > RECENT_AUTH_GRANT_SECONDS:
            raise ValueError("recent-auth grant cannot exceed sixty seconds")
        if _TRUST_EVIDENCE.fullmatch(self.attended_acr) is None:
            raise ValueError("recent-auth authentication evidence is invalid")
        _trusted_issuer(self.attended_issuer)


class AttendedArmReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attended_arm_ref: str
    attended_arm_expires_at: float
    catalog_digest: str
    tool_scope_digest: str
    status: Literal["active", "revoked", "expired"] = "active"

    @field_validator("attended_arm_ref")
    @classmethod
    def _arm_ref(cls, value: str) -> str:
        value = _opaque_ref(value)
        if not value.startswith("attended_"):
            raise ValueError("arm receipt reference must be server generated")
        return value

    @field_validator("attended_arm_expires_at")
    @classmethod
    def _expiry(cls, value: float) -> float:
        if not _timestamps_are_finite((value,)) or value <= 0:
            raise ValueError("arm receipt expiry must be finite and positive")
        return value

    @field_validator("catalog_digest", "tool_scope_digest")
    @classmethod
    def _digests(cls, value: str) -> str:
        if _DIGEST.fullmatch(value) is None:
            raise ValueError("arm receipt digest must be canonical sha256")
        return value


__all__ = ["AttendedArmReceipt", "RecentAuthGrant"]
