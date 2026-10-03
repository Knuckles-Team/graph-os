"""Privacy-safe errors; engine codes derive only from generated provider evidence."""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class GraphOSErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    UNKNOWN_OP = "UNKNOWN_OP"
    SURFACE_NOT_ALLOWED = "SURFACE_NOT_ALLOWED"
    PRINCIPAL_NOT_ALLOWED = "PRINCIPAL_NOT_ALLOWED"
    SCOPE_REQUIRED = "SCOPE_REQUIRED"
    SUBJECT_ACCESS_DENIED = "SUBJECT_ACCESS_DENIED"
    VERB_MISMATCH = "VERB_MISMATCH"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    PLAN_MISMATCH = "PLAN_MISMATCH"
    PLAN_STALE = "PLAN_STALE"
    POLICY_DENIED = "POLICY_DENIED"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    LOAD_CAP_EXCEEDED = "LOAD_CAP_EXCEEDED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INDETERMINATE = "INDETERMINATE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"


# Wire status and retry posture are part of the closed GraphOS vocabulary.
_GRAPHOS_STATUS: dict[GraphOSErrorCode, int] = {
    GraphOSErrorCode.UNAUTHENTICATED: 401,
    GraphOSErrorCode.UNKNOWN_OP: 404,
    GraphOSErrorCode.SURFACE_NOT_ALLOWED: 403,
    GraphOSErrorCode.PRINCIPAL_NOT_ALLOWED: 403,
    GraphOSErrorCode.SCOPE_REQUIRED: 403,
    GraphOSErrorCode.SUBJECT_ACCESS_DENIED: 403,
    GraphOSErrorCode.VERB_MISMATCH: 400,
    GraphOSErrorCode.INVALID_ARGUMENT: 400,
    GraphOSErrorCode.CONFIRMATION_REQUIRED: 428,
    GraphOSErrorCode.STEP_UP_REQUIRED: 428,
    GraphOSErrorCode.PLAN_EXPIRED: 409,
    GraphOSErrorCode.PLAN_MISMATCH: 409,
    GraphOSErrorCode.PLAN_STALE: 409,
    GraphOSErrorCode.POLICY_DENIED: 403,
    GraphOSErrorCode.POLICY_UNAVAILABLE: 503,
    GraphOSErrorCode.LOAD_CAP_EXCEEDED: 409,
    GraphOSErrorCode.UNKNOWN_TOOL: 404,
    GraphOSErrorCode.UNAVAILABLE: 503,
    GraphOSErrorCode.TIMEOUT: 504,
    GraphOSErrorCode.INDETERMINATE: 500,
    GraphOSErrorCode.RATE_LIMITED: 429,
    GraphOSErrorCode.INTERNAL: 500,
}

_RETRYABLE = frozenset(
    {
        GraphOSErrorCode.POLICY_UNAVAILABLE,
        GraphOSErrorCode.TIMEOUT,
        GraphOSErrorCode.RATE_LIMITED,
    }
)


@dataclass(frozen=True, slots=True)
class GraphOSRefusal(Exception):
    code: GraphOSErrorCode
    message: str = "Request refused"
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EngineRefusal(Exception):
    """An engine code whose HTTP status/retryability belong to its contract."""

    code: str
    message: str = "Engine request refused"
    details: Mapping[str, Any] = field(default_factory=dict)
    source: str = field(default="engine", init=False)


def _engine_error(code: str) -> tuple[int, bool]:
    from graph_os.api.registry.contract_admission import _error_entry
    from graph_os.api.registry.eg_binding import EgContractError

    try:
        table = importlib.import_module(
            "graph_os.api.generated.engine_errors"
        ).ENGINE_ERRORS
        entry = table[code]
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise EgContractError("malformed generated engine error entry")
        return _error_entry(code, *entry)
    except (ImportError, AttributeError, KeyError, TypeError) as exc:
        raise EgContractError("engine error is absent from generated contract") from exc


@dataclass(slots=True)
class FleetRefusal(Exception):
    """A child's own code, with its provenance kept in details."""

    code: str
    server: str
    tool: str
    message: str = "Fleet call refused"
    retryable: bool = False


@runtime_checkable
class OpErrorLike(Protocol):
    """Invocation pipeline refusal without importing its implementation."""

    code: str
    details: Mapping[str, Any]


def _public_message(default: str) -> str:
    """Only contract-owned static messages reach callers; dynamic text stays private.

    Existing engine/child messages can contain principal, tenant, credential,
    endpoint, or policy material. Their code remains precise while the public
    text is fixed until a dedicated structured privacy scrub is available.
    """

    return default


_FLEET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_FLEET_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")


def _fleet_details(code: str, details: Mapping[str, Any]) -> dict[str, str]:
    """Project only bounded child provenance, never raw child payloads."""

    if _FLEET_CODE.fullmatch(code) is None:
        raise ValueError("invalid fleet error code")
    server = details.get("server")
    tool = details.get("tool")
    if (
        not isinstance(server, str)
        or _FLEET_NAME.fullmatch(server) is None
        or not isinstance(tool, str)
        or _FLEET_NAME.fullmatch(tool) is None
    ):
        return {}
    return {"server": server, "tool": tool}


def _public_details(
    code: GraphOSErrorCode, details: Mapping[str, Any]
) -> dict[str, Any]:
    """Project only reviewed, bounded fields into a GraphOS refusal."""

    if code in {
        GraphOSErrorCode.CONFIRMATION_REQUIRED,
        GraphOSErrorCode.STEP_UP_REQUIRED,
    }:
        return _confirmation_details(code, details)
    if code == GraphOSErrorCode.INDETERMINATE:
        # The audit adapter emits this fixed reason when it cannot record the
        # outcome of an effect. Never reflect an arbitrary backend message.
        return (
            {"reason": "audit outcome unavailable"}
            if details.get("reason") == "audit outcome unavailable"
            else {}
        )
    allowed = {
        GraphOSErrorCode.SCOPE_REQUIRED: "missing_scopes",
        GraphOSErrorCode.LOAD_CAP_EXCEEDED: "loaded_items",
    }
    key = allowed.get(code)
    if key is None:
        return {}
    values = details.get(key)
    if not isinstance(values, (list, tuple)) or len(values) > 64:
        return {}
    if any(not isinstance(item, str) or len(item) > 128 for item in values):
        return {}
    return {key: list(values)}


def _confirmation_details(
    code: GraphOSErrorCode, details: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep only the bounded lease reference and UI route needed to resume."""

    plan_ref = details.get("plan_ref")
    op = details.get("op")
    effect = details.get("effect")
    if (
        not isinstance(plan_ref, str)
        or re.fullmatch(r"graphos_plan:[0-9a-f]{48}", plan_ref) is None
    ):
        return {}
    if (
        not isinstance(op, str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", op) is None
    ):
        return {}
    if effect not in {"read", "write", "admin", "destructive"}:
        return {}
    projected = {"plan_ref": plan_ref, "op": op, "effect": effect}
    if code == GraphOSErrorCode.STEP_UP_REQUIRED:
        console_url = details.get("console_url")
        if console_url == f"/console/confirm/{plan_ref}":
            projected["console_url"] = console_url
    return projected


def _classify_graphos_refusal(
    error: GraphOSRefusal,
) -> tuple[str, str, int, bool, dict[str, Any]]:
    return (
        error.code.value,
        "graphos",
        _GRAPHOS_STATUS[error.code],
        error.code in _RETRYABLE,
        _public_details(error.code, error.details),
    )


def _classify_fleet_code(
    code: str, *, retryable: bool, details: Mapping[str, Any]
) -> tuple[str, str, int, bool, dict[str, Any]]:
    return code, "fleet", 502, retryable, _fleet_details(code, details)


def _classify_fleet_refusal(
    error: FleetRefusal,
) -> tuple[str, str, int, bool, dict[str, Any]]:
    if not error.code or not error.server or not error.tool:
        raise ValueError("incomplete fleet refusal")
    return _classify_fleet_code(
        error.code,
        retryable=error.retryable,
        details={"server": error.server, "tool": error.tool},
    )


def _classify_graphos_code(
    code: str, details: Mapping[str, Any]
) -> tuple[str, str, int, bool, dict[str, Any]]:
    parsed = GraphOSErrorCode(code)
    return (
        parsed.value,
        "graphos",
        _GRAPHOS_STATUS[parsed],
        parsed in _RETRYABLE,
        _public_details(parsed, details),
    )


def _classify_op_error(
    error: OpErrorLike,
) -> tuple[str, str, int, bool, dict[str, Any]]:
    source = getattr(error, "source", "graphos")
    if source == "engine":
        status, retryable = _engine_error(error.code)
        return error.code, "engine", status, retryable, {}
    if source == "fleet":
        return _classify_fleet_code(error.code, retryable=False, details=error.details)
    if source != "graphos":
        raise ValueError("unknown operation error source")
    return _classify_graphos_code(error.code, error.details)


def _classified_error(
    error: GraphOSRefusal | FleetRefusal | OpErrorLike | Exception,
) -> tuple[str, str, int, bool, dict[str, Any]]:
    if isinstance(error, GraphOSRefusal):
        return _classify_graphos_refusal(error)
    if isinstance(error, FleetRefusal):
        return _classify_fleet_refusal(error)
    if isinstance(error, OpErrorLike):
        return _classify_op_error(error)
    return GraphOSErrorCode.INTERNAL.value, "graphos", 500, False, {}


_RPC_CODES: dict[GraphOSErrorCode, int] = {
    GraphOSErrorCode.UNAUTHENTICATED: -32001,
    GraphOSErrorCode.UNKNOWN_OP: -32601,
    GraphOSErrorCode.SURFACE_NOT_ALLOWED: -32004,
    GraphOSErrorCode.PRINCIPAL_NOT_ALLOWED: -32004,
    GraphOSErrorCode.SCOPE_REQUIRED: -32003,
    GraphOSErrorCode.SUBJECT_ACCESS_DENIED: -32003,
    GraphOSErrorCode.VERB_MISMATCH: -32602,
    GraphOSErrorCode.INVALID_ARGUMENT: -32602,
    GraphOSErrorCode.PLAN_EXPIRED: -32009,
    GraphOSErrorCode.PLAN_MISMATCH: -32009,
    GraphOSErrorCode.PLAN_STALE: -32009,
    GraphOSErrorCode.POLICY_DENIED: -32003,
    GraphOSErrorCode.POLICY_UNAVAILABLE: -32050,
    GraphOSErrorCode.LOAD_CAP_EXCEEDED: -32009,
    GraphOSErrorCode.UNKNOWN_TOOL: -32601,
    GraphOSErrorCode.UNAVAILABLE: -32050,
    GraphOSErrorCode.TIMEOUT: -32051,
    GraphOSErrorCode.INDETERMINATE: -32052,
    GraphOSErrorCode.RATE_LIMITED: -32029,
    GraphOSErrorCode.INTERNAL: -32603,
}


def a2a_error_status(
    code: GraphOSErrorCode | str, *, source: str = "graphos"
) -> tuple[int, int]:
    """Return JSON-RPC and HTTP status without reclassifying engine codes."""

    if source == "engine":
        status, _retryable = _engine_error(str(code))
        return -32000, status
    if source == "fleet":
        if _FLEET_CODE.fullmatch(str(code)) is None:
            raise ValueError("invalid fleet error code")
        return -32000, 502
    if source != "graphos":
        raise ValueError("unknown operation error source")
    typed = GraphOSErrorCode(code)
    return _RPC_CODES[typed], _GRAPHOS_STATUS[typed]


def to_envelope(
    error: GraphOSRefusal | FleetRefusal | OpErrorLike | Exception,
    *,
    op: str,
    request_id: str,
    registry_digest: str,
) -> tuple[int, dict[str, Any]]:
    """Return an HTTP status and a surface-independent failure envelope."""

    if not op or not request_id or not registry_digest:
        raise ValueError("error envelope requires op, request_id, registry_digest")
    code, source, status, retryable, details = _classified_error(error)
    defaults: dict[str, str] = {
        "graphos": "Request refused",
        "fleet": "Fleet call refused",
        "engine": "Engine request refused",
    }
    message = _public_message(defaults[source])
    envelope = {
        "ok": False,
        "error": {
            "code": code,
            "source": source,
            "message": message,
            "retryable": retryable,
            "op": op,
            "request_id": request_id,
            "details": details,
        },
        "meta": {"registry_digest": registry_digest, "api_version": "v1"},
    }
    return status, envelope
