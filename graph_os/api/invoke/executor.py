"""Caller and service execution with a single confused-deputy boundary."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol

from graph_os.api.registry import Executor

from graph_os.api.invoke.steps import (
    FORBIDDEN_OWNER,
    OpError,
    VerifiedCaller,
    forbidden_path,
)


class OperationRuntime(Protocol):
    """Composition root supplies EG-bound contexts and an operation dispatcher."""

    service_scopes: frozenset[str]

    def as_caller(self, caller: VerifiedCaller) -> AbstractAsyncContextManager[Any]: ...

    def as_service(self, tenant: str) -> AbstractAsyncContextManager[Any]: ...

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


def subject_value(op: Any, params: Mapping[str, Any]) -> str | None:
    subject = getattr(op, "subject", None)
    path = getattr(subject, "path", None)
    if not path:
        return None
    value: Any = params
    for segment in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(segment)
    return value if isinstance(value, str) and value else None


async def prepare_executor(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    runtime: OperationRuntime,
) -> OpError | None:
    if op.executor != Executor.SERVICE:
        return None
    owner_field = forbidden_path(params, names=FORBIDDEN_OWNER)
    if owner_field is not None:
        return OpError("INVALID_ARGUMENT", {"field": owner_field})
    if not op.subject:
        return OpError("UNAVAILABLE", {"reason": "service op has no subject"})
    subject = subject_value(op, params)
    if subject is None:
        return OpError("INVALID_ARGUMENT", {"field": "subject"})
    if not set(op.executor_scopes) <= runtime.service_scopes:
        return OpError("UNAVAILABLE", {"reason": "service grant incomplete"})
    try:
        allowed = await runtime.check_subject_access(caller, subject)
    except Exception:
        return OpError("UNAVAILABLE", {"reason": "subject authority unavailable"})
    if not allowed:
        return OpError("SUBJECT_ACCESS_DENIED")
    return None


@asynccontextmanager
async def execution_context(
    op: Any,
    caller: VerifiedCaller,
    runtime: OperationRuntime,
    idempotency_key: str | None = None,
) -> AsyncIterator[ExecutionContext]:
    """The dispatcher receives exactly one validated EG identity context."""

    if op.executor == Executor.SERVICE:
        async with runtime.as_service(caller.tenant) as client:
            yield ExecutionContext(
                caller,
                client,
                caller.principal,
                True,
                getattr(runtime, "bindings", {}),
                idempotency_key,
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
            )


async def recheck_deferred_owner(
    op: Any, params: Mapping[str, Any], owner: VerifiedCaller, runtime: OperationRuntime
) -> OpError | None:
    """Revocation fence for deferred deliveries and scheduled work."""

    missing = sorted(set(op.scopes) - owner.effective_scopes)
    if missing:
        return OpError("SCOPE_REQUIRED", {"missing_scopes": missing})
    return await prepare_executor(op, params, owner, runtime)


ClientFactory = Callable[[str], Awaitable[Any]]
SubjectCheck = Callable[[Any, VerifiedCaller, str], Awaitable[bool]]
EgDispatch = Callable[[Any, Mapping[str, Any], ExecutionContext], Awaitable[Any]]


class BoundOperationRuntime:
    """Explicitly composed runtime; absent authorities have no fallback."""

    def __init__(
        self,
        *,
        caller_client: ClientFactory,
        service_client: ClientFactory,
        check_access: SubjectCheck,
        eg_dispatch: EgDispatch,
        service_scopes: frozenset[str],
        bindings: Mapping[str, Any] | None = None,
    ) -> None:
        self._caller_client = caller_client
        self._service_client = service_client
        self._check_access = check_access
        self._eg_dispatch = eg_dispatch
        self.service_scopes = service_scopes
        self.bindings = bindings or {}

    @asynccontextmanager
    async def as_caller(self, caller: VerifiedCaller) -> AsyncIterator[Any]:
        client = await self._caller_client(caller.tenant)
        with client.use_verified_context(caller.engine_claims):
            yield client

    @asynccontextmanager
    async def as_service(self, tenant: str) -> AsyncIterator[Any]:
        client = await self._service_client(tenant)
        yield client

    async def check_subject_access(self, caller: VerifiedCaller, subject: str) -> bool:
        async with self.as_caller(caller) as client:
            return await self._check_access(client, caller, subject)

    async def dispatch(
        self, op: Any, params: Mapping[str, Any], context: ExecutionContext
    ) -> Any:
        binding = op.binding
        if getattr(binding, "kind", None) == "eg":
            return await self._eg_dispatch(binding, params, context)
        if getattr(binding, "kind", None) != "composite":
            raise RuntimeError("operation has no executable binding")
        module_name, function_name = binding.handler.rsplit(".", 1)
        handler = getattr(import_module(module_name), function_name)
        if inspect.iscoroutinefunction(handler):
            return await handler(context, params, op)
        return await asyncio.to_thread(handler, context, params, op)
