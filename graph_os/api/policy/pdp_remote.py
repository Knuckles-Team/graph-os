"""Eunomia policy decision point for graph-os (EH-629).

A port of the evaluator in AU ``mcp/eunomia_principal.py`` (graph-os must not
import AU's MCP internals). It depends only on the ``eunomia-core`` schemas.

* **Embedded**: one schema-validated, bounded policy file, evaluated in
  process with Eunomia's precedence: explicit deny, then explicit allow, then
  deny.
* **Remote**: the PDP's ``check/bulk`` endpoint through SDK's bounded,
  DNS-pinned HTTP boundary and the runtime ``eunomia`` TLS profile. Any
  transport, status or shape failure answers every check in the batch with a
  denial whose reason is :data:`UNAVAILABLE_REASON`, so an unreachable PDP
  fails closed.

Both expose one method, ``bulk_check``, returning one response per request in
order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from eunomia_core import enums, schemas

__all__ = [
    "UNAVAILABLE_REASON",
    "EmbeddedPolicy",
    "PolicyDecisionPoint",
    "RemotePolicy",
    "evaluate_policies",
    "load_policy_file",
]

UNAVAILABLE_REASON = "authorization service unavailable"
_MAX_POLICY_BYTES = 1024 * 1024
_MAX_BATCH = 100
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


class PolicyDecisionPoint(Protocol):
    async def bulk_check(
        self, requests: Sequence[schemas.CheckRequest]
    ) -> list[schemas.CheckResponse]: ...


def _attribute_value(obj: Any, path: str) -> Any:
    """Resolve Eunomia's dot-path syntax without evaluating user code."""
    current = obj
    for component in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(component)
        elif isinstance(current, list) and component.isdigit():
            index = int(component)
            current = current[index] if index < len(current) else None
        else:
            current = getattr(current, component, None)
        if current is None:
            return None
    return current


def _both_numbers(expected: Any, actual: Any) -> bool:
    def number(value: Any) -> bool:
        return isinstance(value, int | float) and not isinstance(value, bool)

    return number(expected) and number(actual)


def _both_strings(expected: Any, actual: Any) -> bool:
    return isinstance(expected, str) and isinstance(actual, str)


def _any_values(expected: Any, actual: Any) -> bool:
    return True


def _expected_list(expected: Any, actual: Any) -> bool:
    return isinstance(expected, list)


_Op = enums.ConditionOperator
_Check = Callable[[Any, Any], bool]
#: operator -> (the value types it applies to, the comparison)
_OPERATORS: dict[enums.ConditionOperator, tuple[_Check, _Check]] = {
    _Op.EQUALS: (_any_values, lambda e, a: e == a),
    _Op.NOT_EQUALS: (_any_values, lambda e, a: e != a),
    _Op.CONTAINS: (_both_strings, lambda e, a: e in a),
    _Op.NOT_CONTAINS: (_both_strings, lambda e, a: e not in a),
    _Op.STARTS_WITH: (_both_strings, lambda e, a: a.startswith(e)),
    _Op.ENDS_WITH: (_both_strings, lambda e, a: a.endswith(e)),
    _Op.GREATER: (_both_numbers, lambda e, a: e > a),
    _Op.GREATER_OR_EQUAL: (_both_numbers, lambda e, a: e >= a),
    _Op.LESS: (_both_numbers, lambda e, a: e < a),
    _Op.LESS_OR_EQUAL: (_both_numbers, lambda e, a: e <= a),
    _Op.IN: (_expected_list, lambda e, a: a in e),
    _Op.NOT_IN: (_expected_list, lambda e, a: a not in e),
}


def _condition_matches(condition: schemas.Condition, obj: Any) -> bool:
    expected = condition.value
    actual = _attribute_value(obj, condition.path)
    applies, compare = _OPERATORS[condition.operator]
    if expected is None or actual is None or not applies(expected, actual):
        return False
    return bool(compare(expected, actual))


def _rule_matches(rule: schemas.Rule, request: schemas.CheckRequest) -> bool:
    return (
        request.action in rule.actions
        and all(
            _condition_matches(c, request.principal) for c in rule.principal_conditions
        )
        and all(
            _condition_matches(c, request.resource) for c in rule.resource_conditions
        )
    )


def _first_match(
    policy: schemas.Policy, request: schemas.CheckRequest
) -> schemas.Rule | None:
    return next((rule for rule in policy.rules if _rule_matches(rule, request)), None)


def evaluate_policies(
    policies: Sequence[schemas.Policy], request: schemas.CheckRequest
) -> schemas.CheckResponse:
    """Explicit deny > explicit allow > deny (a default effect never allows)."""
    matched = [m for p in policies if (m := _first_match(p, request)) is not None]
    denied = next((r for r in matched if r.effect == enums.PolicyEffect.DENY), None)
    if denied is not None:
        return schemas.CheckResponse(allowed=False, reason=f"rule {denied.name} denied")
    allowed = next((r for r in matched if r.effect == enums.PolicyEffect.ALLOW), None)
    if allowed is not None:
        return schemas.CheckResponse(
            allowed=True, reason=f"rule {allowed.name} allowed"
        )
    return schemas.CheckResponse(allowed=False, reason="no policy allowed the action")


def load_policy_file(path: str) -> schemas.Policy:
    """Read one bounded, schema-valid policy file; raises ``ValueError`` otherwise."""
    candidate = Path(path).expanduser()
    try:
        if not candidate.is_file() or candidate.stat().st_size > _MAX_POLICY_BYTES:
            raise ValueError("Eunomia policy is not a bounded regular file")
        raw = candidate.read_bytes()
    except OSError as exc:
        raise ValueError("Eunomia policy file is unavailable") from exc
    try:
        return schemas.Policy.model_validate_json(raw)
    except Exception as exc:
        raise ValueError("Eunomia policy file is invalid") from exc


class EmbeddedPolicy:
    """An immutable, schema-validated local policy set."""

    def __init__(self, policies: Sequence[schemas.Policy]) -> None:
        if not policies:
            raise ValueError("At least one Eunomia policy is required")
        self._policies = tuple(policies)

    async def bulk_check(
        self, requests: Sequence[schemas.CheckRequest]
    ) -> list[schemas.CheckResponse]:
        return [evaluate_policies(self._policies, request) for request in requests]


def _endpoint_invalid(parsed: SplitResult, host: str) -> bool:
    return any(
        (
            parsed.scheme not in {"http", "https"},
            not host,
            parsed.username is not None or parsed.password is not None,
            bool(parsed.query or parsed.fragment),
            parsed.scheme == "http" and host not in _LOOPBACK,
        )
    )


def _bulk_endpoint(endpoint: str | None) -> str:
    """``<endpoint>/check/bulk``; HTTPS outside loopback, no credentials or query."""
    parsed = urlsplit(str(endpoint or "").strip())
    host = str(parsed.hostname or "").lower().rstrip(".")
    if _endpoint_invalid(parsed, host):
        raise ValueError("Eunomia endpoint is invalid")
    path = f"{parsed.path.rstrip('/')}/check/bulk"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _api_key(reference: str | None) -> str | None:
    """Resolve the PDP API key from a runtime secret reference, never a value."""
    if not reference:
        return None
    from agent_connector_sdk.credentials.resolution import resolve_secret_reference

    try:
        value = resolve_secret_reference(reference)
    except Exception as exc:
        raise ValueError("Eunomia API key reference could not be resolved") from exc
    if not 16 <= len(value) <= 4_096 or any(c in value for c in "\r\n\x00"):
        raise ValueError("Eunomia API key reference resolved to an invalid value")
    return value


class RemotePolicy:
    """A remote PDP; every failure is a fail-closed denial."""

    def __init__(
        self, endpoint: str | None, config: Any, transport: Any = None
    ) -> None:
        self._url = _bulk_endpoint(endpoint)
        key = _api_key(getattr(config, "eunomia_api_key_ref", None))
        self._headers = {"WAY-API-KEY": key} if key else {}
        self._private_hosts = tuple(
            getattr(config, "eunomia_allowed_private_hosts", ())
        )
        self._timeout = float(getattr(config, "eunomia_timeout_seconds", 10.0))
        self._max_bytes = int(getattr(config, "eunomia_max_response_bytes", 1 << 20))
        self._batch = min(
            int(getattr(config, "eunomia_bulk_check_max", _MAX_BATCH)), _MAX_BATCH
        )
        self._transport = transport

    async def _post(
        self, chunk: Sequence[schemas.CheckRequest]
    ) -> list[schemas.CheckResponse]:
        from agent_connector_sdk.http.source_post import safe_post_json_async

        profile = None
        if self._transport is None:
            from agent_utilities.core.transport_security import (
                resolve_configured_tls_profile,
            )

            profile = resolve_configured_tls_profile("eunomia")
        try:
            response = await safe_post_json_async(
                self._url,
                [request.model_dump(mode="json") for request in chunk],
                headers=self._headers,
                timeout=self._timeout,
                max_bytes=self._max_bytes,
                max_request_bytes=self._max_bytes,
                allowed_private_hosts=self._private_hosts,
                transport=self._transport,
                tls=profile,
            )
        finally:
            if profile is not None:
                profile.cleanup()
        if not isinstance(response, list) or len(response) != len(chunk):
            raise ValueError("remote authorization response was misaligned")
        return [schemas.CheckResponse.model_validate(item) for item in response]

    async def bulk_check(
        self, requests: Sequence[schemas.CheckRequest]
    ) -> list[schemas.CheckResponse]:
        selected = list(requests)
        try:
            merged: list[schemas.CheckResponse] = []
            for start in range(0, len(selected), self._batch):
                merged.extend(await self._post(selected[start : start + self._batch]))
        except Exception:
            unavailable = schemas.CheckResponse(
                allowed=False, reason=UNAVAILABLE_REASON
            )
            return [unavailable for _ in selected]
        return merged
