"""Caller and service execution with a single confused-deputy boundary."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import Any, Protocol

from graph_os.api.invoke.steps import (
    FORBIDDEN_OWNER,
    OpError,
    VerifiedCaller,
    claim_values,
    forbidden_path,
    freeze_claims,
)
from graph_os.api.registry import Executor, SubjectSource


class OperationRuntime(Protocol):
    """Composition root supplies EG-bound contexts and an operation dispatcher."""

    service_scopes: frozenset[str]

    async def verify_current(self, caller: VerifiedCaller) -> VerifiedCaller: ...

    def as_caller(self, caller: VerifiedCaller) -> AbstractAsyncContextManager[Any]: ...

    def as_service(
        self, tenant: str, *, required_scopes: frozenset[str]
    ) -> AbstractAsyncContextManager[Any]: ...

    async def check_subject_access(
        self, caller: VerifiedCaller, subject: str
    ) -> bool: ...

    async def dispatch(
        self, op: Any, params: Mapping[str, Any], context: ExecutionContext
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    caller: VerifiedCaller
    client: Any
    owner: str
    service_identity: bool
    services: Mapping[str, Any]
    idempotency_key: str | None = None
    fleet_decision: Any = None
    registry_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))

    @property
    def owner_ref(self) -> str:
        """EG persistence key for the verified owner, never from call parameters."""

        return "principal:sha256:" + hashlib.sha256(self.owner.encode()).hexdigest()


def subject_value(
    op: Any, params: Mapping[str, Any], caller: VerifiedCaller
) -> str | None:
    """Resolve a declared subject from params or verified caller authority."""

    subject = getattr(op, "subject", None)
    if subject is None:
        return None
    if subject.source == SubjectSource.CALLER_TENANT:
        return caller.tenant
    if subject.source != SubjectSource.PARAM:
        return None
    path = getattr(subject, "path", None)
    if not path:
        return None
    value: Any = params
    segments = path.split(".")
    if segments[0] == "params":
        segments = segments[1:]
    for segment in segments:
        if not isinstance(value, Mapping):
            return None
        value = value.get(segment)
    return value if isinstance(value, str) and value else None


async def prepare_executor(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    runtime: OperationRuntime,
    verified_subject: str | None = None,
) -> OpError | None:
    if op.executor != Executor.SERVICE:
        return None
    owner_field = forbidden_path(params, names=FORBIDDEN_OWNER)
    if owner_field is not None:
        return OpError("INVALID_ARGUMENT", {"field": owner_field})
    if verified_subject is not None and op.id != "fleet.call":
        return OpError("UNAVAILABLE", {"reason": "unexpected resolved subject"})
    if not op.scopes or not op.executor_scopes:
        return OpError("UNAVAILABLE", {"reason": "service authority absent"})
    if not op.subject and verified_subject is None:
        return OpError("UNAVAILABLE", {"reason": "service op has no subject"})
    subject = verified_subject or subject_value(op, params, caller)
    if subject is None:
        return OpError("INVALID_ARGUMENT", {"field": "subject"})
    if not set(op.executor_scopes) <= runtime.service_scopes:
        return OpError("UNAVAILABLE", {"reason": "service grant incomplete"})
    try:
        allowed = await runtime.check_subject_access(caller, subject)
    except Exception:
        return OpError("UNAVAILABLE", {"reason": "subject authority unavailable"})
    if allowed is not True:
        return OpError("SUBJECT_ACCESS_DENIED")
    return None


@asynccontextmanager
async def execution_context(
    op: Any,
    caller: VerifiedCaller,
    runtime: OperationRuntime,
    idempotency_key: str | None = None,
    fleet_decision: Any = None,
    registry_digest: str = "",
) -> AsyncIterator[ExecutionContext]:
    """The dispatcher receives exactly one validated EG identity context."""

    if op.executor == Executor.SERVICE:
        async with runtime.as_service(
            caller.tenant, required_scopes=op.executor_scopes
        ) as client:
            yield ExecutionContext(
                caller,
                client,
                caller.principal,
                True,
                getattr(runtime, "bindings", {}),
                idempotency_key,
                fleet_decision,
                registry_digest,
            )
    else:
        async with runtime.as_caller(caller) as client:
            yield ExecutionContext(
                caller,
                client,
                caller.principal,
                False,
                getattr(runtime, "bindings", {}),
                idempotency_key,
                fleet_decision,
                registry_digest,
            )


ClientFactory = Callable[[str], Awaitable[Any]]
ServiceClaims = Callable[[str, frozenset[str]], Awaitable[Mapping[str, Any]]]
VerifyCurrent = Callable[[VerifiedCaller], Awaitable[VerifiedCaller]]
SubjectCheck = Callable[[Any, VerifiedCaller, str], Awaitable[bool]]
EgDispatch = Callable[[Any, Mapping[str, Any], ExecutionContext], Awaitable[Any]]


class BoundOperationRuntime:
    """Explicitly composed runtime; absent authorities have no fallback."""

    def __init__(
        self,
        *,
        caller_client: ClientFactory,
        verify_current: VerifyCurrent,
        service_client: ClientFactory,
        service_claims: ServiceClaims,
        check_access: SubjectCheck,
        eg_dispatch: EgDispatch,
        service_scopes: frozenset[str],
        service_principal: str,
        bindings: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(service_principal, str) or not service_principal:
            raise ValueError("verified service principal is required")
        if any(
            not callable(port)
            for port in (
                verify_current,
                caller_client,
                service_client,
                service_claims,
                check_access,
                eg_dispatch,
            )
        ):
            raise ValueError("required runtime authority unavailable")
        if any(
            not isinstance(scope, str) or not scope or "*" in scope
            for scope in service_scopes
        ):
            raise ValueError("service allowlist requires exact scopes")
        self._service_principal = service_principal
        self._verify_current = verify_current
        self._caller_client = caller_client
        self._service_client = service_client
        self._service_claims = service_claims
        self._check_access = check_access
        self._eg_dispatch = eg_dispatch
        self.service_scopes = frozenset(service_scopes)
        self._bindings = MappingProxyType(dict(bindings or {}))

    @property
    def bindings(self) -> Mapping[str, Any]:
        """The exact admitted service-name mapping; owner objects retain identity."""
        return self._bindings

    async def verify_current(self, caller: VerifiedCaller) -> VerifiedCaller:
        return await self._verify_current(caller)

    @asynccontextmanager
    async def as_caller(self, caller: VerifiedCaller) -> AsyncIterator[Any]:
        client = await self._caller_client(caller.tenant)
        with client.use_verified_context(claim_values(caller.engine_claims)):
            yield client

    @asynccontextmanager
    async def as_service(
        self, tenant: str, *, required_scopes: frozenset[str]
    ) -> AsyncIterator[Any]:
        if not required_scopes or not required_scopes <= self.service_scopes:
            raise PermissionError("service scope is not allowlisted")
        claims = freeze_claims(await self._service_claims(tenant, required_scopes))
        scopes = claims.get("scopes")
        if (
            claims.get("tenant") != tenant
            or claims.get("principal") != self._service_principal
            or not isinstance(scopes, (list, tuple, set, frozenset))
            or frozenset(scopes) != required_scopes
        ):
            raise PermissionError("verified graph-os service authority is unavailable")
        client = await self._service_client(tenant)
        with client.use_verified_context(claim_values(claims)):
            yield client

    async def check_subject_access(self, caller: VerifiedCaller, subject: str) -> bool:
        async with self.as_caller(caller) as client:
            return await self._check_access(client, caller, subject)

    async def dispatch(
        self, op: Any, params: Mapping[str, Any], context: ExecutionContext
    ) -> Any:
        binding = op.binding
        if getattr(binding, "kind", None) == "eg":
            from epistemic_graph import EngineResponseError

            from graph_os.api.errors import EngineRefusal

            try:
                return await self._eg_dispatch(binding, params, context)
            except EngineResponseError as exc:
                raise EngineRefusal(exc.code) from exc
        if getattr(binding, "kind", None) != "composite":
            raise RuntimeError("operation has no executable binding")
        module_name, function_name = binding.handler.rsplit(".", 1)
        handler = getattr(import_module(module_name), function_name)
        if inspect.iscoroutinefunction(handler):
            return await handler(context, params, op)
        return await asyncio.to_thread(handler, context, params, op)
