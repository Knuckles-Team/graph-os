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
        if any(not isinstance(key, str) for key in value):
            raise ValueError("authority keys must be strings")
        return MappingProxyType(
            {key: freeze_claims(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_claims(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_claims(child) for child in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("authority must contain only immutable JSON values")


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
    if forbidden_path(params) is not None:
        return OpError("INVALID_ARGUMENT", {"field": forbidden_path(params)})
    model = op.params
    if isinstance(model, EgSchemaRef):
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
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        return OpError("UNAVAILABLE", {"reason": "schema binding unavailable"})
    try:
        return cast(type[BaseModel], model).model_validate(params, extra="forbid")
    except ValidationError as exc:
        return OpError(
            "INVALID_ARGUMENT",
            {"fields": [".".join(map(str, item["loc"])) for item in exc.errors()]},
        )


def forbidden_path(
    value: Any, prefix: str = "", names: frozenset[str] = FORBIDDEN_AUTHORITY
) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in names:
                return path
            nested = forbidden_path(child, path, names)
            if nested is not None:
                return nested
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            nested = forbidden_path(child, f"{prefix}[{index}]", names)
            if nested is not None:
                return nested
    return None


def authenticate(caller: VerifiedCaller | None) -> OpError | None:
    if not isinstance(caller, VerifiedCaller) or caller.authenticated is not True:
        return OpError("UNAUTHENTICATED")
    claims = caller.engine_claims
    if not isinstance(claims, Mapping) or caller.principal_kind not in {
        "human",
        "service",
    }:
        return OpError("UNAUTHENTICATED")
    if (
        claims.get("principal") != caller.principal
        or claims.get("tenant") != caller.tenant
    ):
        return OpError("UNAUTHENTICATED")
    scopes = claims.get("scopes")
    if (
        not isinstance(scopes, (list, tuple, set, frozenset))
        or any(
            not isinstance(scope, str) or not scope or "*" in scope for scope in scopes
        )
        or frozenset(scopes) != caller.effective_scopes
    ):
        return OpError("UNAUTHENTICATED")
    if claims.get("policy_version") != caller.policy_revision:
        return OpError("UNAUTHENTICATED")
    if bool(claims.get("delegation")) != caller.delegated:
        return OpError("UNAUTHENTICATED")
    if any(
        not isinstance(value, str) or not value
        for value in (
            caller.principal,
            caller.tenant,
            caller.policy_revision,
            caller.credential_kind,
        )
    ) or not isinstance(caller.delegated, bool):
        return OpError("UNAUTHENTICATED")
    return None


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
