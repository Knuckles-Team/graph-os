"""Immutable operation index and fail-closed discovery filtering."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol

from .digest import canonical_registry
from .spec import OpSpec, PrincipalRule, Surface, Verb


class Caller(Protocol):
    """The verified identity facts discovery needs; supplied by a surface adapter."""

    @property
    def effective_scopes(self) -> frozenset[str]: ...

    @property
    def principal_kind(self) -> str: ...

    @property
    def delegated(self) -> bool: ...


PolicyDecision = Callable[[OpSpec, Caller], bool]

# The single invocation chokepoint (GRAPHOS-OPS-R007): one op id, its params,
# a verified caller and the serving surface in, an OpResult or OpError-shaped
# outcome out. Surface adapters depend on this shape rather than importing
# the invocation pipeline's own module, so an HTTP or MCP projection can land
# and be tested against a fake before that pipeline exists.
Invoke = Callable[..., Awaitable[Any]]


def authorized(op: OpSpec, caller: Caller, *, policy: PolicyDecision) -> bool:
    """Apply principal, exact-scope and policy checks before disclosing an op."""

    kind = caller.principal_kind
    if kind not in {"human", "service"}:
        return False
    if op.principals is PrincipalRule.SERVICE_ONLY and kind != "service":
        return False
    if (
        op.principals in {PrincipalRule.HUMAN, PrincipalRule.HUMAN_UNDELEGATED}
        and kind != "human"
    ):
        return False
    if op.principals is PrincipalRule.HUMAN_UNDELEGATED and caller.delegated:
        return False
    if not op.scopes.issubset(caller.effective_scopes):
        return False
    try:
        return policy(op, caller) is True
    except Exception:
        return False


class Registry:
    """An immutable registry whose discovery always requires a policy decision."""

    def __init__(
        self,
        ops: Iterable[OpSpec],
        *,
        api_version: str = "1",
        contract_root: Path | None = None,
    ) -> None:
        index: dict[str, OpSpec] = {}
        for op in ops:
            if op.id in index:
                raise ValueError(f"duplicate operation id: {op.id}")
            index[op.id] = op
        self._ops = index
        self.api_version = api_version
        self.canonical = canonical_registry(
            index.values(), api_version=api_version, contract_root=contract_root
        )
        self.digest = hashlib.sha256(self.canonical).hexdigest()

    def __getitem__(self, op_id: str) -> OpSpec:
        return self._ops[op_id]

    def get(self, op_id: str) -> OpSpec | None:
        return self._ops.get(op_id)

    def __iter__(self) -> Iterator[OpSpec]:
        return iter(sorted(self._ops.values(), key=lambda op: op.id))

    def __len__(self) -> int:
        return len(self._ops)

    def find(
        self,
        caller: Caller,
        *,
        policy: PolicyDecision,
        verb: Verb | None = None,
        surface: Surface | None = None,
    ) -> tuple[OpSpec, ...]:
        """Return only operations authorized for this verified caller and policy."""

        return tuple(
            op
            for op in self
            if (verb is None or op.verb is verb)
            and (surface is None or surface in op.surfaces)
            and authorized(op, caller, policy=policy)
        )
