"""Parse EG's authoritative resolution shape without inventing authority.

Parsing is not authentication. Only the qualified credential-resolution port
may supply these values to an issuer; a user lookup or request body cannot.
The public EG specialization is IdentityReply<RequestContextClaims>, carrying
PrincipalResolution<RequestContextClaims> under kind=resolution/value. The
internal unit-context specialization serializes a null context and is refused.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

_CONTEXT_REQUIRED = frozenset(
    {
        "principal",
        "tenant",
        "audience",
        "agent_id",
        "roles",
        "scopes",
        "policy_version",
        "delegation",
    }
)
_RESOLUTION_FIELDS = frozenset(
    {
        "principal_id",
        "username",
        "kind",
        "status",
        "is_bootstrap",
        "roles",
        "groups",
        "scopes",
        "mfa_required",
        "mfa_enrolled",
        "session_mfa_pending",
        "request_context",
    }
)


class IdentityUnavailable(RuntimeError):
    """Required authoritative response is absent or outside its contract."""


def _text(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise IdentityUnavailable("identity field must be a nonempty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise IdentityUnavailable("identity field contains control characters")
    return value


def _strings(value: Any, *, scopes: bool = False) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        raise IdentityUnavailable("identity set must be an explicit array")
    result = tuple(_text(item) for item in value)
    if len(set(result)) != len(result):
        raise IdentityUnavailable("identity set contains duplicates")
    if scopes and any(
        "*" in item or any(char.isspace() for char in item) for item in result
    ):
        raise IdentityUnavailable("scope must be an exact capability token")
    return result


def _shape(value: Any, required: frozenset[str], optional: frozenset[str]) -> None:
    if not isinstance(value, Mapping):
        raise IdentityUnavailable("identity response must be an object")
    if not required <= value.keys() or value.keys() - required - optional:
        raise IdentityUnavailable("identity response fields disagree with contract")


def _context(value: Any) -> Mapping[str, Any]:
    _shape(value, _CONTEXT_REQUIRED, frozenset({"node", "priority"}))
    result: dict[str, Any] = {}
    for name in _CONTEXT_REQUIRED - {"roles", "scopes", "delegation"}:
        result[name] = _text(value[name])
    for name in ("roles", "scopes", "delegation"):
        result[name] = _strings(value[name], scopes=name == "scopes")
    for name in ("node", "priority"):
        if name in value:
            result[name] = None if value[name] is None else _text(value[name])
    # This slice has no qualified per-hop delegation authority.
    if result["agent_id"] != result["principal"] or result["delegation"]:
        raise IdentityUnavailable("delegated issuance authority is unavailable")
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True, repr=False)
class Resolution:
    """Immutable parsed response, not a constructible authentication proof."""

    principal_id: str
    username: str
    kind: str
    status: str
    is_bootstrap: bool
    roles: tuple[str, ...]
    groups: tuple[str, ...]
    scopes: tuple[str, ...]
    mfa_required: bool
    mfa_enrolled: bool
    session_mfa_pending: bool
    request_context: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("principal_id", "username", "kind", "status"):
            _text(getattr(self, name))
        if self.kind not in {"human", "service"}:
            raise IdentityUnavailable("unknown principal kind")
        if self.status != "active":
            raise PermissionError("principal is not active")
        for name in (
            "is_bootstrap",
            "mfa_required",
            "mfa_enrolled",
            "session_mfa_pending",
        ):
            if type(getattr(self, name)) is not bool:
                raise IdentityUnavailable("identity flag must be an explicit boolean")
        if self.session_mfa_pending or (self.mfa_required and not self.mfa_enrolled):
            raise PermissionError("required second factor is incomplete")
        for name in ("roles", "groups", "scopes"):
            object.__setattr__(
                self, name, _strings(getattr(self, name), scopes=name == "scopes")
            )
        context = _context(self.request_context)
        if context["principal"] != self.principal_id or any(
            set(context[name]) != set(getattr(self, name))
            for name in ("roles", "scopes")
        ):
            raise IdentityUnavailable("resolution and request context disagree")
        object.__setattr__(self, "request_context", context)

    @classmethod
    def parse(cls, value: Any) -> "Resolution":
        _shape(value, _RESOLUTION_FIELDS, frozenset())
        return cls(**dict(value))

    @classmethod
    def from_reply(cls, reply: Any) -> "Resolution":
        _shape(reply, frozenset({"kind", "value"}), frozenset())
        if reply["kind"] != "resolution":
            raise IdentityUnavailable("identity reply is not a resolution")
        return cls.parse(reply["value"])

    def narrow(self, scopes: tuple[str, ...]) -> "Resolution":
        """Intersect without reconstructing authority from roles."""
        ceiling = _strings(scopes, scopes=True)
        effective = tuple(sorted(set(self.scopes) & set(ceiling)))
        return replace(
            self,
            scopes=effective,
            request_context={**self.request_context, "scopes": effective},
        )
