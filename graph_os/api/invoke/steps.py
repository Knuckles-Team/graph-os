"""Authority and argument checks shared by every operation transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from graph_os.api.registry import EgSchemaRef, PrincipalRule
from pydantic import BaseModel, ValidationError

FORBIDDEN_AUTHORITY = frozenset({"_actor", "_roles", "_tenant", "principal", "tenant"})
FORBIDDEN_OWNER = frozenset({"owner", "owner_ref"})


@dataclass(frozen=True, slots=True)
class VerifiedCaller:
    """Authority supplied by an authenticated surface adapter, never by params."""

    principal: str
    tenant: str
    effective_scopes: frozenset[str]
    engine_claims: Mapping[str, Any]
    principal_kind: str
    authenticated: bool = True
    delegated: bool = False
    credential_kind: str = "session"
    policy_revision: str = ""
    request_id: str = ""
    mfa_at_ms: int | None = None
    session: Any = field(default=None, compare=False, repr=False)

    @classmethod
    def from_session(
        cls, session: Any, *, request_id: str = "", mfa_at_ms: int | None = None
    ) -> VerifiedCaller:
        """Snapshot only server-verified session authority for a surface adapter."""

        session.ensure_authority_current()
        actor = session.actor
        actor.ensure_credential_current()
        if not actor.authenticated:
            raise PermissionError("verified caller requires authenticated actor")
        claims = session.engine_verified_context()
        kind = "human" if str(actor.actor_type) == "human" else "service"
        return cls(
            principal=actor.actor_id,
            tenant=session.tenant,
            effective_scopes=frozenset(session.scopes),
            engine_claims=claims,
            principal_kind=kind,
            delegated=bool(claims.get("delegation")),
            policy_revision=session.policy_version,
            request_id=request_id,
            mfa_at_ms=mfa_at_ms,
            session=session,
        )


@dataclass(frozen=True, slots=True)
class OpError:
    code: str
    details: Mapping[str, Any] = field(default_factory=dict)


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
    elif isinstance(value, list):
        for index, child in enumerate(value):
            nested = forbidden_path(child, f"{prefix}[{index}]", names)
            if nested is not None:
                return nested
    return None


def authenticate(caller: VerifiedCaller | None) -> OpError | None:
    if caller is None or not caller.authenticated:
        return OpError("UNAUTHENTICATED")
    if caller.session is not None:
        try:
            caller.session.ensure_authority_current()
        except Exception:
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
        or frozenset(scopes) != caller.effective_scopes
    ):
        return OpError("UNAUTHENTICATED")
    if claims.get("policy_version", caller.policy_revision) != caller.policy_revision:
        return OpError("UNAUTHENTICATED")
    if bool(claims.get("delegation")) != caller.delegated:
        return OpError("UNAUTHENTICATED")
    if not caller.principal or not caller.tenant:
        return OpError("UNAUTHENTICATED")
    return None


def principal_rule(op: Any, caller: VerifiedCaller) -> OpError | None:
    rule = op.principals
    is_service = caller.principal_kind == "service"
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
