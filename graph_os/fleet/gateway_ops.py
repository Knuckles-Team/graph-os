"""Effect classification for governed fleet calls.

The caller supplies an exact child descriptor from the admitted catalog. This
module does not dispatch a child call: the invoke pipeline must classify and
confirm the effect before any child can be reached.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.registry import Confirm, Effect, PrincipalRule


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


ToolFor = Callable[[str, str, Any], Awaitable[AdmittedTool]]
PolicyCheck = Callable[[str, str, Any], Awaitable[bool]]
DelegatedCall = Callable[[str, str, Mapping[str, Any], Any], Awaitable[Any]]


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
    ) -> None:
        self._tool_for = tool_for
        self._policy_check = policy_check
        self._delegated_call = delegated_call

    async def effect(
        self, _op: Any, params: Mapping[str, Any], caller: Any
    ) -> tuple[Effect, Confirm, PrincipalRule]:
        descriptor = await self._tool_for(params["server"], params["tool"], caller)
        return annotation_effect(
            descriptor.annotations, override=descriptor.effect_override
        )

    async def call(
        self, caller: Any, server: str, tool: str, arguments: Mapping[str, Any]
    ) -> Any:
        if "mcp:delegate" not in caller.effective_scopes:
            raise PermissionError("mcp:delegate is required")
        descriptor = await self._tool_for(server, tool, caller)
        if not descriptor.required_scopes.issubset(caller.effective_scopes):
            raise PermissionError("child scopes are required")
        if descriptor.credential_mode != "delegated":
            raise PermissionError("service-credential child needs SERVICE binding")
        if await self._policy_check(server, tool, caller) is not True:
            raise PermissionError("fleet call denied by policy")
        return await self._delegated_call(server, tool, arguments, caller)


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
