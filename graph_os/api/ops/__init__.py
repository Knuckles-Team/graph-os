"""Supported GraphOS declarations and the offline canonical registry factory."""

from __future__ import annotations

from . import write_back_models
from .registry_factory import get_registry

__all__ = ["get_registry", "write_back_models"]
