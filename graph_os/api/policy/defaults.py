"""Identity-profile defaults for the API policy decision point."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def policy_mode(identity_mode: str, configured: str | None = None) -> str:
    """Resolve an explicit policy mode or the identity profile's safe default."""
    if identity_mode not in {"none", "local", "external"}:
        raise ValueError("unknown identity mode")
    mode = (
        configured
        if configured is not None
        else ("none" if identity_mode == "none" else "embedded")
    )
    if mode not in {"none", "embedded", "remote"}:
        raise ValueError("unknown Eunomia mode")
    return mode


def default_policy_file() -> Path:
    """The shipped local-profile policy, loaded as package data."""
    return Path(str(files("graph_os.api.policy").joinpath("default_policy.json")))
