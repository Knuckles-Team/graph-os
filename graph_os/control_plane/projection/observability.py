"""Discrepancy-observation port for control-plane projection."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["DriftSink"]


@runtime_checkable
class DriftSink(Protocol):
    """Durable/observable sink for bounded projection discrepancies."""

    def record(self, drift: object) -> None:
        """Persist one typed drift record without raw exception text."""
