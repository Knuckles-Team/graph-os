"""Shared validation and enums for the browser-control protocol."""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from typing import Any, Literal, cast
from urllib.parse import urlsplit

PROTOCOL_VERSION: Literal["webmcp.control.v1"] = "webmcp.control.v1"
MAX_MESSAGE_BYTES = 65_536
MAX_SCHEMA_BYTES = 16_384
DEFAULT_LEASE_SECONDS = 300
MAX_LEASE_SECONDS = 900
RECENT_AUTH_GRANT_SECONDS = 60
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OPAQUE_REF = re.compile(
    r"^(?:pref_[a-z0-9_]+_[a-f0-9]{64}|[a-z][a-z0-9_]{0,31}_[a-f0-9]{64}|[a-f0-9]{64})$"
)
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESULT_ERROR_CODE = _ERROR_CODE
_TRUST_EVIDENCE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")


def _timestamps_are_finite(values: tuple[float, ...]) -> bool:
    return all(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        for value in values
    )


def _canonical_value(value: Any) -> Any:
    """Return the closed cross-runtime JSON subset in canonical key order."""

    if value is None or isinstance(value, (bool, str)):
        return _canonical_scalar(value)
    if value.__class__ is int:
        return _canonical_integer(value)
    if value.__class__ is float:
        raise ValueError("browser control protocol does not accept floating numbers")
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return _canonical_object(value)
    raise ValueError("browser control values must use the closed JSON subset")


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, str):
        _valid_unicode(value, label="strings")
    return value


def _canonical_integer(value: int) -> int:
    if -MAX_SAFE_JSON_INTEGER <= value <= MAX_SAFE_JSON_INTEGER:
        return value
    raise ValueError("browser control integers must be JS-safe")


def _valid_unicode(value: str, *, label: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"browser control {label} must be valid Unicode") from exc


def _canonical_object(value: dict[Any, Any]) -> dict[str, Any]:
    if any(not isinstance(key, str) for key in value):
        raise ValueError("browser control object keys must be strings")
    keys = cast(list[str], list(value))
    for key in keys:
        _valid_unicode(key, label="object keys")
    # JavaScript Array.sort compares UTF-16 code units.
    keys.sort(key=lambda key: key.encode("utf-16-be"))
    return {key: _canonical_value(value[key]) for key in keys}


def canonical_json(value: Any) -> str:
    """Serialize the byte-identical Python/JavaScript protocol JSON subset."""

    try:
        return json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("browser control values must be finite JSON") from exc


def canonical_internal_json(value: Any) -> str:
    """Serialize server-only identity values that never cross runtimes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("browser control identity must be finite JSON") from exc


def content_sha256(value: Any) -> str:
    """Return the public content digest used by the cross-repository protocol."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def schema_sha256(input_schema: dict[str, Any], output_schema: dict[str, Any]) -> str:
    """Digest the exact input/output schema pair advertised by a tool."""

    digest = content_sha256(
        {"input_schema": input_schema, "output_schema": output_schema}
    )
    return f"sha256:{digest}"


def _bounded_json(value: Any, *, limit: int = MAX_MESSAGE_BYTES) -> Any:
    if len(canonical_json(value).encode("utf-8")) > limit:
        raise ValueError("browser control JSON exceeds its byte limit")
    return value


def _identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("value must be a bounded protocol identifier")
    return value


def _opaque_ref(value: str) -> str:
    if _OPAQUE_REF.fullmatch(value) is None:
        raise ValueError("value must be an opaque persistence reference")
    return value


def _trusted_issuer(value: str) -> str:
    parsed = urlsplit(value)
    if (
        _TRUST_EVIDENCE.fullmatch(value) is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("attended authentication issuer must be canonical HTTPS")
    return value


class MutationClass(StrEnum):
    READ = "read"
    LOCAL_UI_MUTATION = "local-ui-mutation"


class ConfirmationPolicy(StrEnum):
    NONE = "none"
    EXACT_REQUEST = "exact-request"


class CancellationEffect(StrEnum):
    NONE = "none"
    BROWSER_REPORTED_COMMITTED = "browser_reported_committed"
    UNKNOWN = "unknown"


class LangfuseStatus(StrEnum):
    RECORDED = "recorded"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "MAX_LEASE_SECONDS",
    "MAX_MESSAGE_BYTES",
    "MAX_SAFE_JSON_INTEGER",
    "MAX_SCHEMA_BYTES",
    "PROTOCOL_VERSION",
    "RECENT_AUTH_GRANT_SECONDS",
    "CancellationEffect",
    "ConfirmationPolicy",
    "LangfuseStatus",
    "MutationClass",
    "_DIGEST",
    "_ERROR_CODE",
    "_RESULT_ERROR_CODE",
    "_TRUST_EVIDENCE",
    "_bounded_json",
    "_identifier",
    "_opaque_ref",
    "_timestamps_are_finite",
    "_trusted_issuer",
    "canonical_json",
    "canonical_internal_json",
    "content_sha256",
    "schema_sha256",
]
