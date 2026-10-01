"""GraphOS operation contract and shared surface adapters."""

from graph_os.api.errors import (
    FleetRefusal,
    GraphOSErrorCode,
    GraphOSRefusal,
    OpErrorLike,
    a2a_error_status,
    to_envelope,
)

__all__ = [
    "FleetRefusal",
    "GraphOSErrorCode",
    "GraphOSRefusal",
    "OpErrorLike",
    "a2a_error_status",
    "to_envelope",
]
