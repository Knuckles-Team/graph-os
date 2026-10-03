"""Effect classification for governed fleet calls.

The caller supplies an exact child descriptor from the admitted catalog. This
module does not dispatch a child call: the invoke pipeline must classify and
confirm the effect before any child can be reached.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from graph_os.api.errors import FleetRefusal
from graph_os.api.registry import Confirm, Effect, Executor, PrincipalRule

if TYPE_CHECKING:
    from graph_os.api.invoke import FleetCallDecision

_CHILD_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")


NativeDispatchFence = Callable[[str, str, Any, bool], None]
_NATIVE_DISPATCH: ContextVar[NativeDispatchFence | None] = ContextVar(
    "graphos_native_fleet_dispatch", default=None
)


@contextmanager
def native_dispatch_scope(fence: NativeDispatchFence) -> Iterator[None]:
    """Carry the existing session owner's grant only for this native invocation."""
    token = _NATIVE_DISPATCH.set(fence)
    try:
        yield
    finally:
        _NATIVE_DISPATCH.reset(token)


def commit_native_dispatch(server: str, tool: str, caller: Any) -> None:
    fence = _NATIVE_DISPATCH.get()
    if fence is not None:
        fence(server, tool, caller, True)


def check_native_transport(server: str, tool: str) -> None:
    """Recheck after transport setup awaits, immediately before the MCP request."""
    fence = _NATIVE_DISPATCH.get()
    if fence is not None:
        fence(server, tool, None, False)


def _child_error_code(result: Any) -> str:
    """Read only a structured child code; never infer one from free text."""

    structured = getattr(
        result, "structured_content", getattr(result, "structuredContent", None)
    )
    if not isinstance(structured, Mapping):
        return "CHILD_REFUSED"
    error = structured.get("error")
    typed = error if isinstance(error, Mapping) else structured
    code = typed.get("code")
    return (
        code
        if isinstance(code, str) and _CHILD_CODE.fullmatch(code)
        else "CHILD_REFUSED"
    )


def _hint(annotations: Any, snake: str, camel: str) -> bool | None:
    if isinstance(annotations, Mapping):
        value = annotations.get(camel, annotations.get(snake))
    else:
        value = getattr(annotations, snake, getattr(annotations, camel, None))
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("invalid child tool annotation")
    return value


def annotation_effect(
    annotations: Any, *, override: str | None = None
) -> tuple[Effect, Confirm, PrincipalRule]:
    """Classify a child call conservatively, before invoke's effect gate.

    Manifest overrides are only admitted from the trusted child catalog, not
    from request parameters. Unknown or unannotated tools are writes.
    """
    if override is not None:
        if override not in {"read", "write", "destructive", "admin"}:
            raise ValueError("invalid fleet effect override")
        effect = Effect(override)
    elif _hint(annotations, "destructive_hint", "destructiveHint") is True:
        effect = Effect.DESTRUCTIVE
    elif _hint(annotations, "read_only_hint", "readOnlyHint") is True:
        effect = Effect.READ
    else:
        effect = Effect.WRITE
    confirm = {
        Effect.READ: Confirm.NONE,
        Effect.WRITE: Confirm.NONE,
        Effect.DESTRUCTIVE: Confirm.PLAN,
        Effect.ADMIN: Confirm.CONSOLE,
    }[effect]
    principal = (
        PrincipalRule.HUMAN_UNDELEGATED if effect is Effect.ADMIN else PrincipalRule.ANY
    )
    return effect, confirm, principal


DescriptorFor = Callable[[str, str, Any], Awaitable[tuple[Any, str | None]]]


@dataclass(frozen=True, slots=True)
class AdmittedTool:
    """Caller-filtered descriptor from the trusted live fleet catalog."""

    annotations: Any
    effect_override: str | None
    required_scopes: frozenset[str]
    credential_mode: str
    executor_scopes: frozenset[str] = frozenset()
    subject_id: str | None = None


ToolFor = Callable[[str, str, Any], Awaitable[AdmittedTool]]
PolicyCheck = Callable[[str, str, Any], Awaitable[bool]]
DelegatedCall = Callable[[str, str, Mapping[str, Any], Any], Awaitable[Any]]
# A service callback must stamp every durable child record with the verified
# owner's canonical principal reference. The generic multiplexer dispatcher
# cannot prove that property and must not be bound here.
ServiceCall = Callable[[str, str, Mapping[str, Any], Any, str, str], Awaitable[Any]]


def _require_admitted_target(item: Any, server: str, tool: str) -> None:
    if (
        item is None
        or getattr(item, "kind", None) != "tool"
        or getattr(item, "server", None) != server
        or getattr(item, "name", None) != tool
    ):
        raise PermissionError("fleet tool is not admitted")


def _require_service_authority(
    mode: str, executor_scopes: frozenset[str], subject_id: Any
) -> None:
    if mode == "service" and (
        not isinstance(executor_scopes, frozenset)
        or not executor_scopes
        or not isinstance(subject_id, str)
        or not subject_id
    ):
        raise RuntimeError("service child authority metadata is incomplete")


def _require_descriptor_authority(descriptor: AdmittedTool) -> None:
    if (
        not isinstance(descriptor, AdmittedTool)
        or not isinstance(descriptor.required_scopes, frozenset)
        or not all(
            isinstance(scope, str) and scope for scope in descriptor.required_scopes
        )
        or descriptor.credential_mode not in {"delegated", "service"}
        or not isinstance(descriptor.executor_scopes, frozenset)
        or not all(
            isinstance(scope, str) and scope for scope in descriptor.executor_scopes
        )
    ):
        raise RuntimeError("fleet tool authority metadata is incomplete")
    _require_service_authority(
        descriptor.credential_mode, descriptor.executor_scopes, descriptor.subject_id
    )
    if descriptor.credential_mode == "service" and not descriptor.required_scopes:
        raise RuntimeError("service child caller scopes are required")
    if descriptor.credential_mode == "delegated" and (
        descriptor.executor_scopes or descriptor.subject_id is not None
    ):
        raise RuntimeError("delegated child cannot carry service authority")


def tool_for_multiplexer_ops(ops: Any) -> ToolFor:
    """Read private effect/credential metadata from the admitted fleet item.

    The service must derive these fields from verified EG manifest and live
    child probe data. None of them may come from call arguments or a public
    catalog response.
    """

    async def tool_for(server: str, tool: str, caller: Any) -> AdmittedTool:
        item = await ops.admitted_tool(caller, server, tool)
        _require_admitted_target(item, server, tool)
        scopes = getattr(item, "required_scopes", None)
        mode = getattr(item, "credential_mode", None)
        if not isinstance(scopes, frozenset) or mode not in {"delegated", "service"}:
            raise RuntimeError("fleet tool authority metadata is incomplete")
        executor_scopes: frozenset[str] = getattr(item, "executor_scopes", frozenset())
        subject_id = getattr(item, "subject_id", None)
        _require_service_authority(mode, executor_scopes, subject_id)
        return AdmittedTool(
            annotations=getattr(item, "annotations", None),
            effect_override=getattr(item, "effect_override", None),
            required_scopes=scopes,
            credential_mode=mode,
            executor_scopes=executor_scopes,
            # Catalog component identity also exists for delegated children;
            # it is not a service-executor subject grant.
            subject_id=subject_id if mode == "service" else None,
        )

    return tool_for


def oauth_delegated_call_for_mux(mux: Any) -> DelegatedCall:
    """Use only the per-principal OAuth child path; never the shared pool.

    ``call_oauth_gated_tool`` opens an ephemeral caller-grant session. A static
    service child or malformed OAuth declaration fails inside that path.
    """

    async def delegated(
        server: str, tool: str, arguments: Mapping[str, Any], caller: Any
    ) -> Any:
        from agent_utilities.api import use_session
        from agent_utilities.security.brain_context import use_actor

        session = caller.session
        if session is None:
            raise PermissionError("verified delegated session is required")
        actor = session.actor
        if (
            actor.authenticated is not True
            or str(actor.actor_id) != caller.principal
            or str(actor.tenant_id) != caller.tenant
        ):
            raise PermissionError("delegated caller does not match verified session")
        with use_actor(actor), use_session(session):
            result = await mux.call_oauth_gated_tool(server, tool, dict(arguments))
        if bool(getattr(result, "is_error", getattr(result, "isError", False))):
            raise FleetRefusal(_child_error_code(result), server, tool)
        return result.model_dump(mode="json", by_alias=True)

    return delegated


def _owner_identity_confirmed(
    *,
    caller: Any,
    service_identity: bool,
    owner: str | None,
    owner_ref: str | None,
    expected_owner_ref: str,
) -> bool:
    return (
        service_identity is True
        and owner == caller.principal
        and owner_ref == expected_owner_ref
    )


def _fleet_decision_matches(fleet_decision: Any, descriptor: AdmittedTool) -> bool:
    return (
        fleet_decision is not None
        and fleet_decision.credential_mode == "service"
        and fleet_decision.executor is Executor.SERVICE
        and fleet_decision.subject_id == descriptor.subject_id
        and fleet_decision.executor_scopes == descriptor.executor_scopes
        and fleet_decision.required_scopes == descriptor.required_scopes
    )


def _validated_service_owner_ref(
    *,
    caller: Any,
    descriptor: AdmittedTool,
    service_identity: bool,
    owner: str | None,
    owner_ref: str | None,
    fleet_decision: Any,
    registry_digest: str,
) -> str:
    """Return ``owner_ref`` once every service-credential authority matches.

    Returning the confirmed value (rather than leaving the caller's own
    ``str | None`` in scope) is what lets the one dispatch call that follows
    pass it to a transport expecting a plain ``str``.
    """
    expected_owner_ref = (
        "principal:sha256:" + hashlib.sha256(caller.principal.encode()).hexdigest()
    )
    identity_ok = _owner_identity_confirmed(
        caller=caller,
        service_identity=service_identity,
        owner=owner,
        owner_ref=owner_ref,
        expected_owner_ref=expected_owner_ref,
    )
    if (
        not identity_ok
        or not _fleet_decision_matches(fleet_decision, descriptor)
        or not registry_digest
    ):
        raise PermissionError("service child authority changed before dispatch")
    return expected_owner_ref


class FleetGateway:
    """One direct-call authority boundary around an injected child dispatcher.

    The delegated dispatcher must use the caller's credential. Service-credential
    children remain closed until a separate owner-stamped SERVICE binding exists.
    """

    def __init__(
        self,
        *,
        tool_for: ToolFor,
        policy_check: PolicyCheck,
        delegated_call: DelegatedCall,
        catalog_ops: Any | None = None,
        service_call: ServiceCall | None = None,
    ) -> None:
        self._tool_for = tool_for
        self._policy_check = policy_check
        self._delegated_call = delegated_call
        self._catalog_ops = catalog_ops
        self._service_call = service_call

    def _catalog(
        self, caller: Any, scope: str, *, session_required: bool = False
    ) -> Any:
        if scope not in caller.effective_scopes:
            raise PermissionError(f"{scope} is required")
        if session_required and caller.session is None:
            raise PermissionError("verified fleet session is required")
        if self._catalog_ops is None:
            raise RuntimeError("fleet catalog operations are not bound")
        return self._catalog_ops

    async def search(self, caller: Any, **params: Any) -> Any:
        return await self._catalog(caller, "mcp:discover").search(caller, **params)

    async def list(self, caller: Any, **params: Any) -> Any:
        return await self._catalog(caller, "mcp:discover").list(caller, **params)

    async def status(self, caller: Any, **params: Any) -> Any:
        return await self._catalog(caller, "mcp:discover").status(caller, **params)

    async def load(self, caller: Any, **params: Any) -> Any:
        return await self._catalog(caller, "mcp:delegate", session_required=True).load(
            caller, **params
        )

    async def unload(self, caller: Any, **params: Any) -> Any:
        if not any(
            (
                params.get("items"),
                params.get("servers"),
                params.get("kinds"),
                params.get("all_items"),
            )
        ):
            raise ValueError("unload requires a target")
        return await self._catalog(
            caller, "mcp:delegate", session_required=True
        ).unload(caller, **params)

    async def effect(
        self, _op: Any, params: Mapping[str, Any], caller: Any
    ) -> FleetCallDecision:
        """Resolve B's canonical decision using current admitted authority only."""
        from graph_os.api.invoke import FleetCallDecision

        descriptor = await self._admitted_tool(caller, params["server"], params["tool"])
        effect, confirm, principal = annotation_effect(
            descriptor.annotations, override=descriptor.effect_override
        )
        service = descriptor.credential_mode == "service"
        if service and self._service_call is None:
            raise RuntimeError("owner-stamped service child adapter is unavailable")
        return FleetCallDecision(
            effect=effect,
            confirm=confirm,
            principals=principal,
            executor=Executor.SERVICE if service else Executor.CALLER,
            required_scopes=descriptor.required_scopes,
            executor_scopes=descriptor.executor_scopes,
            subject_id=descriptor.subject_id,
            credential_mode=descriptor.credential_mode,
        )

    async def _admitted_tool(self, caller: Any, server: str, tool: str) -> AdmittedTool:
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        if caller.session is None:
            raise PermissionError("verified fleet session is required")
        descriptor = await self._tool_for(server, tool, caller)
        _require_descriptor_authority(descriptor)
        if not descriptor.required_scopes.issubset(caller.effective_scopes):
            raise PermissionError("child scopes are required")
        if await self._policy_check(server, tool, caller) is not True:
            raise PermissionError("fleet call denied by policy")
        return descriptor

    async def call(
        self,
        caller: Any,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        expected_effect: Effect,
        service_identity: bool = False,
        owner: str | None = None,
        owner_ref: str | None = None,
        fleet_decision: Any = None,
        registry_digest: str = "",
    ) -> Any:
        try:
            descriptor = await self._admitted_tool(caller, server, tool)
        except RuntimeError as exc:
            raise PermissionError("fleet authority changed before dispatch") from exc
        current_effect, _, _ = annotation_effect(
            descriptor.annotations, override=descriptor.effect_override
        )
        if current_effect is not expected_effect:
            raise RuntimeError("fleet effect changed before dispatch")
        if descriptor.credential_mode == "service":
            if self._service_call is None:
                raise PermissionError("service child authority changed before dispatch")
            validated_owner_ref = _validated_service_owner_ref(
                caller=caller,
                descriptor=descriptor,
                service_identity=service_identity,
                owner=owner,
                owner_ref=owner_ref,
                fleet_decision=fleet_decision,
                registry_digest=registry_digest,
            )
            commit_native_dispatch(server, tool, caller)
            return await self._service_call(
                server, tool, arguments, caller, validated_owner_ref, registry_digest
            )
        if service_identity is True:
            raise PermissionError("delegated child cannot use service identity")
        commit_native_dispatch(server, tool, caller)
        return await self._delegated_call(server, tool, arguments, caller)


def compose_fleet_gateway(
    *,
    ops: Any,
    mux: Any,
    policy_check: PolicyCheck,
    service_call: ServiceCall | None = None,
) -> FleetGateway:
    """Assemble the only supported caller-credential fleet boundary.

    The served root must supply the same verified multiplexer operation service
    used by the resident tools and an enabled call-policy check. Absent pieces
    cannot be substituted with permissive defaults.
    """
    if ops is None or mux is None or policy_check is None:
        raise ValueError("fleet gateway authorities are required")
    return FleetGateway(
        tool_for=tool_for_multiplexer_ops(ops),
        policy_check=policy_check,
        delegated_call=oauth_delegated_call_for_mux(mux),
        catalog_ops=ops,
        # Static child credentials stay unavailable until an owner-stamped,
        # typed service-child adapter is explicitly supplied.
        service_call=service_call,
    )


def fleet_effect_for(
    descriptor_for: DescriptorFor,
) -> Callable[
    [Any, Mapping[str, Any], Any], Awaitable[tuple[Effect, Confirm, PrincipalRule]]
]:
    """Legacy annotation classifier, not the canonical FleetEffect port.

    Serving composition binds FleetGateway.effect instead: annotations alone
    cannot establish credential mode, exact scopes, or service subject authority.
    """

    async def resolve(
        _op: Any, params: Mapping[str, Any], caller: Any
    ) -> tuple[Effect, Confirm, PrincipalRule]:
        server = params.get("server")
        tool = params.get("tool")
        if (
            not isinstance(server, str)
            or not server
            or not isinstance(tool, str)
            or not tool
        ):
            raise ValueError("fleet target is required")
        annotations, override = await descriptor_for(server, tool, caller)
        return annotation_effect(annotations, override=override)

    return resolve
