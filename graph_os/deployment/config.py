"""GraphOS-owned hosting profile selection.

Agent Utilities owns agent and model settings. GraphOS owns the topology of
the process that serves those agents, including the deployment profile.
"""

from __future__ import annotations

from dataclasses import dataclass

PROFILES = ("tiny", "single-node-prod", "enterprise")


@dataclass(frozen=True, slots=True)
class HostingProfileError(ValueError):
    """A stable, credential-free hosting configuration failure."""

    code: str


def resolve_deployment_profile(profile: str | None, app_profile: str) -> str:
    """Select an explicit topology, allowing the tiny development default."""
    if not profile and app_profile in {"prod", "production"}:
        raise HostingProfileError("deployment_profile_required")
    selected = str(profile or "tiny").strip()
    if selected not in PROFILES:
        raise HostingProfileError("deployment_profile_invalid")
    return selected
