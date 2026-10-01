"""One narrowing policy decision for API operations and fleet catalog items.

Adapters pass verified caller facts and call this gate after exact scopes and
principal-kind checks. The gate repeats those checks for discovery and loading,
so an omitted adapter check cannot expose an item. It never grants authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from eunomia_core import schemas

from graph_os.api.policy.cache import DecisionCache
from graph_os.api.policy.defaults import default_policy_file, policy_mode
from graph_os.api.policy.pdp_remote import (
    UNAVAILABLE_REASON,
    EmbeddedPolicy,
    RemotePolicy,
    load_policy_file,
)

EUNOMIA_BULK_CHECK_MAX = 100


class PolicyUnavailable(RuntimeError):
    """Enabled policy point could not produce an authoritative decision."""


class DecisionPoint(Protocol):
    async def bulk_check(
        self, requests: Sequence[schemas.CheckRequest]
    ) -> list[schemas.CheckResponse]: ...


@dataclass(frozen=True, slots=True)
class PolicyItem:
    resource: str
    required_scopes: frozenset[str]
    principal_rule: str = "any"
    effect: str = "read"
    attributes: Mapping[str, Any] | None = None


def op_resource(op: Any) -> PolicyItem:
    """Stable PDP identity for a GraphOS operation."""
    effect = str(getattr(op.effect, "value", op.effect))
    rule = str(getattr(op.principals, "value", op.principals))
    return PolicyItem(
        resource=f"op:{op.id}",
        required_scopes=frozenset(op.scopes),
        principal_rule=rule,
        effect=effect,
        attributes={"op_id": op.id, "effect": effect},
    )


def fleet_resource(
    kind: str,
    name: str,
    *,
    required_scopes: Iterable[str],
    effect: str = "read",
    principal_rule: str = "any",
) -> PolicyItem:
    """Canonical fleet resource; children retain their own exact scopes."""
    if kind not in {"tool", "prompt", "resource", "skill", "connector"}:
        raise ValueError("unknown fleet item kind")
    if not name or name.startswith("/") or ".." in name:
        raise ValueError("invalid fleet item name")
    prefix = {
        "tool": "fleet:tool:",
        "prompt": "fleet:prompt:",
        "resource": "fleet:resource:",
        "skill": "skill:",
        "connector": "connector:",
    }[kind]
    resource = f"{prefix}{name}"
    return PolicyItem(
        resource=resource,
        required_scopes=frozenset(required_scopes),
        principal_rule=principal_rule,
        effect=effect,
        attributes={
            "kind": kind,
            "effect": effect,
            "destructive_fleet": effect in {"destructive", "admin"},
        },
    )


def _caller_value(caller: Any, name: str, default: Any = None) -> Any:
    return getattr(caller, name, default)


def _principal(caller: Any) -> schemas.PrincipalCheck:
    principal = str(_caller_value(caller, "principal", ""))
    tenant = str(_caller_value(caller, "tenant", ""))
    if not principal or not tenant:
        raise PolicyUnavailable("verified principal and tenant required")
    return schemas.PrincipalCheck(
        uri=f"principal:{principal}",
        attributes={
            "id": principal,
            "kind": _caller_value(caller, "principal_kind", ""),
            "tenant": tenant,
            "groups": sorted(_caller_value(caller, "groups", ())),
            "roles": sorted(_caller_value(caller, "roles", ())),
            "scopes": sorted(_caller_value(caller, "effective_scopes", ())),
            "is_admin": "mcp:admin" in _caller_value(caller, "effective_scopes", ()),
            "auth_mode": _caller_value(caller, "auth_mode", ""),
            "delegated": bool(_caller_value(caller, "delegated", False)),
        },
    )


def _resource(item: PolicyItem) -> schemas.ResourceCheck:
    return schemas.ResourceCheck(
        uri=item.resource,
        attributes={"uri": item.resource, **dict(item.attributes or {})},
    )


def _base_authorized(item: PolicyItem, caller: Any, action: str) -> bool:
    if not _caller_value(caller, "authenticated", True):
        return False
    kind = _caller_value(caller, "principal_kind", "")
    if kind not in {"human", "service"}:
        return False
    if item.principal_rule == "service_only" and kind != "service":
        return False
    if item.principal_rule in {"human", "human_undelegated"} and kind != "human":
        return False
    if item.principal_rule == "human_undelegated" and _caller_value(
        caller, "delegated", False
    ):
        return False
    scopes = frozenset(_caller_value(caller, "effective_scopes", ()))
    needed = item.required_scopes
    if action in {"discover", "load"}:
        needed = needed | {"mcp:discover"}
    if action == "load":
        needed = needed | {"mcp:delegate"}
    return needed.issubset(scopes)


class PolicyGate:
    """Enforce exact authority then optional Eunomia for every operation."""

    def __init__(
        self,
        mode: str,
        decision_point: DecisionPoint | None = None,
        *,
        cache: DecisionCache | None = None,
        on_revision_change: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        if mode not in {"none", "embedded", "remote"}:
            raise ValueError("unknown Eunomia mode")
        self.mode = mode
        self._pdp = decision_point
        self._cache = cache or DecisionCache()
        self._on_revision_change = on_revision_change

    @classmethod
    def from_config(
        cls, config: Any, identity_mode: str, *, configured_mode: str | None = None
    ) -> PolicyGate:
        """Create the PDP; explicit config is required to override profile defaults."""
        mode = policy_mode(identity_mode, configured_mode)
        if mode == "none":
            return cls(mode)
        if mode == "remote":
            return cls(
                mode, RemotePolicy(getattr(config, "eunomia_remote_url", None), config)
            )
        path = getattr(config, "eunomia_policy_file", None) or default_policy_file()
        return cls(mode, EmbeddedPolicy([load_policy_file(str(path))]))

    async def _revision(self, caller: Any) -> str:
        revision = str(_caller_value(caller, "policy_revision", ""))
        if not revision:
            raise PolicyUnavailable("policy revision unavailable")
        if (
            self._cache.set_revision(str(caller.tenant), revision)
            and self._on_revision_change is not None
        ):
            await self._on_revision_change(revision)
        return revision

    async def decisions(
        self, items: Sequence[PolicyItem], caller: Any, action: str
    ) -> list[bool]:
        if action not in {"discover", "load", "call"}:
            raise ValueError("unknown policy action")
        selected = list(items)
        allowed = [_base_authorized(item, caller, action) for item in selected]
        if self.mode == "none" or not any(allowed):
            return allowed
        if self._pdp is None:
            raise PolicyUnavailable("policy decision point unavailable")
        revision = await self._revision(caller)
        principal = _principal(caller)
        for start in range(0, len(selected), EUNOMIA_BULK_CHECK_MAX):
            positions = [
                index
                for index in range(
                    start, min(start + EUNOMIA_BULK_CHECK_MAX, len(selected))
                )
                if allowed[index]
            ]
            await self._decide_chunk(
                selected, positions, allowed, caller, principal, revision, action
            )
        return allowed

    async def _decide_chunk(
        self,
        items: Sequence[PolicyItem],
        positions: list[int],
        allowed: list[bool],
        caller: Any,
        principal: schemas.PrincipalCheck,
        revision: str,
        action: str,
    ) -> None:
        if not positions:
            return
        authority_digest = hashlib.sha256(
            principal.model_dump_json().encode()
        ).hexdigest()
        uncached: list[tuple[int, tuple[str, str, str, str, str]]] = []
        requests: list[schemas.CheckRequest] = []
        for index in positions:
            item = items[index]
            key = (
                authority_digest,
                str(caller.tenant),
                revision,
                f"{item.resource}:{item.effect}",
                action,
            )
            cached = (
                None
                if item.effect in {"destructive", "admin"}
                else self._cache.get(key)
            )
            if cached is not None:
                allowed[index] = cached
                continue
            uncached.append((index, key))
            requests.append(
                schemas.CheckRequest(
                    principal=principal, resource=_resource(item), action=action
                )
            )
        if not requests:
            return
        try:
            responses = await self._pdp.bulk_check(requests) if self._pdp else []
        except Exception as exc:
            raise PolicyUnavailable("policy decision point unavailable") from exc
        if len(responses) != len(requests):
            raise PolicyUnavailable("policy response alignment failed")
        for (index, key), response in zip(uncached, responses, strict=True):
            if not isinstance(response, schemas.CheckResponse):
                raise PolicyUnavailable("policy response invalid")
            if response.reason == UNAVAILABLE_REASON:
                raise PolicyUnavailable("policy decision point unavailable")
            allowed[index] = response.allowed is True
            if items[index].effect not in {"destructive", "admin"}:
                self._cache.put(key, allowed[index])

    async def check_op(self, op: Any, caller: Any) -> bool:
        """The invoke pipeline's policy_check callback."""
        return (await self.decisions([op_resource(op)], caller, "call"))[0]

    async def visible(self, items: Sequence[PolicyItem], caller: Any) -> list[bool]:
        return await self.decisions(items, caller, "discover")

    async def loadable(self, items: Sequence[PolicyItem], caller: Any) -> list[bool]:
        return await self.decisions(items, caller, "load")
