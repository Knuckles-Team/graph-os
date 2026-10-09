"""Authority and argument checks shared by every operation transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

from graph_os.api.registry import EgSchemaRef, PrincipalRule

FORBIDDEN_AUTHORITY = frozenset({"_actor", "_roles", "_tenant", "principal", "tenant"})
FORBIDDEN_OWNER = frozenset({"owner", "owner_ref"})


def freeze_claims(value: Any) -> Any:
    """Own immutable JSON-shaped authority; never retain caller aliases."""
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_claims(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_claims(child) for child in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("authority must contain only immutable JSON values")


def _freeze_mapping(value: Mapping[Any, Any]) -> Mapping[str, Any]:
    if any(not isinstance(key, str) for key in value):
        raise ValueError("authority keys must be strings")
    return MappingProxyType({key: freeze_claims(child) for key, child in value.items()})


def claim_values(value: Any) -> Any:
    """Copy frozen authority into the public client's JSON carrier."""
    if isinstance(value, Mapping):
        return {key: claim_values(child) for key, child in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [claim_values(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class VerifiedCaller:
    """Authority supplied by an authenticated surface adapter, never by params."""

    principal: str
    tenant: str
    effective_scopes: frozenset[str]
    engine_claims: Mapping[str, Any]
    principal_kind: str
    authenticated: bool
    delegated: bool
    credential_kind: str
    policy_revision: str
    request_id: str = ""
    mfa_at_ms: int | None = None
    session: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "engine_claims", freeze_claims(self.engine_claims))
        object.__setattr__(self, "effective_scopes", frozenset(self.effective_scopes))


@dataclass(frozen=True, slots=True)
class OpError:
    code: str
    details: Mapping[str, Any] = field(default_factory=dict)
    source: Literal["graphos", "engine", "fleet"] = "graphos"


@dataclass(frozen=True, slots=True)
class OpResult:
    value: Any = None
    code: str = "OK"
    details: Mapping[str, Any] = field(default_factory=dict)


SchemaValidate = Callable[[EgSchemaRef, Mapping[str, Any]], Mapping[str, Any]]


def validate_params(
    op: Any,
    params: Mapping[str, Any],
    schema_validate: SchemaValidate | None = None,
) -> BaseModel | Mapping[str, Any] | OpError:
    """Reject authority asserted in input, including nested owner fields."""

    if not isinstance(params, Mapping):
        return OpError("INVALID_ARGUMENT")
    asserted = forbidden_path(params)
    if asserted is not None:
        return OpError("INVALID_ARGUMENT", {"field": asserted})
    model = op.params
    if isinstance(model, EgSchemaRef):
        return _validate_provider(model, params, schema_validate)
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        return OpError("UNAVAILABLE", {"reason": "schema binding unavailable"})
    try:
        return cast(type[BaseModel], model).model_validate(params, extra="forbid")
    except ValidationError as exc:
        return OpError(
            "INVALID_ARGUMENT",
            {"fields": [".".join(map(str, item["loc"])) for item in exc.errors()]},
        )


def _validate_provider(
    model: EgSchemaRef,
    params: Mapping[str, Any],
    schema_validate: SchemaValidate | None,
) -> Mapping[str, Any] | OpError:
    if schema_validate is None:
        return OpError("UNAVAILABLE", {"reason": "EG schema validator unavailable"})
    try:
        validated = schema_validate(model, params)
    except ValueError:
        return OpError("INVALID_ARGUMENT")
    except Exception:
        return OpError("UNAVAILABLE", {"reason": "schema authority unavailable"})
    if not isinstance(validated, Mapping) or forbidden_path(validated) is not None:
        return OpError("INVALID_ARGUMENT")
    return validated


def _children(value: Any, prefix: str) -> list[tuple[Any, str, Any]]:
    """Yield (key, path, child) for one container level; scalars have none."""
    if isinstance(value, Mapping):
        return [
            (key, f"{prefix}.{key}" if prefix else str(key), child)
            for key, child in value.items()
        ]
    if isinstance(value, (list, tuple)):
        return [
            (None, f"{prefix}[{index}]", child) for index, child in enumerate(value)
        ]
    return []


def forbidden_path(
    value: Any, prefix: str = "", names: frozenset[str] = FORBIDDEN_AUTHORITY
) -> str | None:
    for key, path, child in _children(value, prefix):
        if key in names:
            return path
        nested = forbidden_path(child, path, names)
        if nested is not None:
            return nested
    return None


def authenticate(caller: VerifiedCaller | None) -> OpError | None:
    if not isinstance(caller, VerifiedCaller) or caller.authenticated is not True:
        return OpError("UNAUTHENTICATED")
    if _claims_match(caller) and _identity_fields_valid(caller):
        return None
    return OpError("UNAUTHENTICATED")


def _claims_match(caller: VerifiedCaller) -> bool:
    claims = caller.engine_claims
    if not isinstance(claims, Mapping) or caller.principal_kind not in {
        "human",
        "service",
    }:
        return False
    return (
        claims.get("principal") == caller.principal
        and claims.get("tenant") == caller.tenant
        and _scopes_match(claims.get("scopes"), caller.effective_scopes)
        and claims.get("policy_version") == caller.policy_revision
        and bool(claims.get("delegation")) == caller.delegated
    )


def _scopes_match(scopes: Any, effective: frozenset[str]) -> bool:
    return (
        isinstance(scopes, (list, tuple, set, frozenset))
        and all(
            isinstance(scope, str) and bool(scope) and "*" not in scope
            for scope in scopes
        )
        and frozenset(scopes) == effective
    )


def _identity_fields_valid(caller: VerifiedCaller) -> bool:
    fields = (
        caller.principal,
        caller.tenant,
        caller.policy_revision,
        caller.credential_kind,
    )
    return all(isinstance(value, str) and bool(value) for value in fields) and (
        isinstance(caller.delegated, bool)
    )


def principal_rule(op: Any, caller: VerifiedCaller) -> OpError | None:
    rule = op.principals
    is_service = caller.principal_kind == "service"
    if rule not in set(PrincipalRule):
        return OpError("PRINCIPAL_NOT_ALLOWED")
    denied = (
        (rule == PrincipalRule.HUMAN and is_service)
        or (
            rule == PrincipalRule.HUMAN_UNDELEGATED and (is_service or caller.delegated)
        )
        or (rule == PrincipalRule.SERVICE_ONLY and not is_service)
    )
    if denied:
        return OpError("PRINCIPAL_NOT_ALLOWED")
    return None


def require_scopes(op: Any, caller: VerifiedCaller) -> OpError | None:
    missing = sorted(set(op.scopes) - caller.effective_scopes)
    return OpError("SCOPE_REQUIRED", {"missing_scopes": missing}) if missing else None
