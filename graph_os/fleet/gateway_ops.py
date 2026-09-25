"""Effect classification for governed fleet calls.

The caller supplies an exact child descriptor from the admitted catalog. This
module does not dispatch a child call: the invoke pipeline must classify and
confirm the effect before any child can be reached.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.registry import Confirm, Effect, Executor, PrincipalRule


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


def tool_for_multiplexer_ops(ops: Any) -> ToolFor:
    """Read private effect/credential metadata from the admitted fleet item.

    The service must derive these fields from verified EG manifest and live
    child probe data. None of them may come from call arguments or a public
    catalog response.
    """

    async def tool_for(server: str, tool: str, caller: Any) -> AdmittedTool:
        item = await ops.admitted_tool(caller, server, tool)
        if (
            item is None
            or getattr(item, "kind", None) != "tool"
            or getattr(item, "server", None) != server
            or getattr(item, "name", None) != tool
        ):
            raise PermissionError("fleet tool is not admitted")
        scopes = getattr(item, "required_scopes", None)
        mode = getattr(item, "credential_mode", None)
        if not isinstance(scopes, frozenset) or mode not in {"delegated", "service"}:
            raise RuntimeError("fleet tool authority metadata is incomplete")
        executor_scopes = getattr(item, "executor_scopes", frozenset())
        subject_id = getattr(item, "subject_id", None)
        if mode == "service" and (
            not isinstance(executor_scopes, frozenset)
            or not executor_scopes
            or not isinstance(subject_id, str)
            or not subject_id
        ):
            raise RuntimeError("service child authority metadata is incomplete")
        return AdmittedTool(
            annotations=getattr(item, "annotations", None),
            effect_override=getattr(item, "effect_override", None),
            required_scopes=scopes,
            credential_mode=mode,
            executor_scopes=executor_scopes,
            subject_id=subject_id,
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
            raise RuntimeError("delegated child tool failed")
        return result.model_dump(mode="json", by_alias=True)

    return delegated


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

    async def effect(self, _op: Any, params: Mapping[str, Any], caller: Any) -> Any:
        descriptor = await self._admitted_tool(caller, params["server"], params["tool"])
        effect, confirm, principal = annotation_effect(
            descriptor.annotations, override=descriptor.effect_override
        )
        if descriptor.credential_mode == "service":
            if self._service_call is None:
                raise RuntimeError("owner-stamped service child adapter is unavailable")
            from graph_os.api.invoke import FleetCallDecision

            return FleetCallDecision(
                effect=effect,
                confirm=confirm,
                principals=principal,
                executor=Executor.SERVICE,
                required_scopes=descriptor.required_scopes,
                executor_scopes=descriptor.executor_scopes,
                subject_id=descriptor.subject_id,
                credential_mode="service",
            )
        return effect, confirm, principal

    async def _admitted_tool(self, caller: Any, server: str, tool: str) -> AdmittedTool:
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        if caller.session is None:
            raise PermissionError("verified fleet session is required")
        descriptor = await self._tool_for(server, tool, caller)
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
        descriptor = await self._admitted_tool(caller, server, tool)
        current_effect, _, _ = annotation_effect(
            descriptor.annotations, override=descriptor.effect_override
        )
        if current_effect is not expected_effect:
            raise RuntimeError("fleet effect changed before dispatch")
        if descriptor.credential_mode == "service":
            expected_owner_ref = (
                "principal:sha256:"
                + hashlib.sha256(caller.principal.encode()).hexdigest()
            )
            if (
                service_identity is not True
                or owner != caller.principal
                or owner_ref != expected_owner_ref
                or fleet_decision is None
                or fleet_decision.credential_mode != "service"
                or fleet_decision.executor is not Executor.SERVICE
                or fleet_decision.subject_id != descriptor.subject_id
                or fleet_decision.executor_scopes != descriptor.executor_scopes
                or fleet_decision.required_scopes != descriptor.required_scopes
                or self._service_call is None
                or not registry_digest
            ):
                raise PermissionError("service child authority changed before dispatch")
            return await self._service_call(
                server, tool, arguments, caller, owner_ref, registry_digest
            )
        if service_identity is True:
            raise PermissionError("delegated child cannot use service identity")
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
    """Bind the invoke hook to a caller-filtered, fresh catalog lookup."""

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
