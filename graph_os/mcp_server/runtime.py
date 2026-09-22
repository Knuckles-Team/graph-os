#!/usr/bin/python
"""GraphOS MCP server runtime: tool registry, dispatch and host routes.

The server is built by the agent-connector-sdk factory with GraphOS host
policy (:mod:`graph_os.mcp_server.serving`). Every registered action tool is
dispatched through :func:`_execute_tool` under the verified caller session,
and each has exactly one REST twin in :data:`ACTION_TOOL_ROUTES`.

Usage::

    graph-os --transport stdio
    graph-os --transport streamable-http --host 127.0.0.1 --port 8004
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import threading
import uuid
from types import MappingProxyType
from typing import Any, cast

from agent_utilities.core.config import setting

from graph_os._version import __version__

logger = logging.getLogger(__name__)


REGISTERED_TOOLS: dict[str, Any] = {}


# Server-side authority for stdio MCP, minted once from configured runtime
# secret-reference/OAuth2 identity. Network requests receive their session from middleware and
# never fall back to this process authority.
_PROCESS_SESSION: Any = None
_PROCESS_SESSION_REFRESH_LOCK = threading.Lock()
_PROCESS_AUTHORITY_STOP = threading.Event()
_PROCESS_AUTHORITY_THREAD: threading.Thread | None = None

# D-SNV-5 follow-up: guards :func:`authority_keepalive_scope` against starting a
# second renewal loop for the same lease when one guarded scope nests inside
# another on the same task tree (for example an MCP tool dispatch whose tool body
# itself calls ``Orchestrator.execute_agent`` for a sub-delegation). contextvars
# propagate across ``await``, ``asyncio.ensure_future``/``create_task``, and
# ``asyncio.to_thread`` (which copies the current context into the worker thread),
# so the guard is visible to a nested scope even when the nesting crosses a
# to_thread boundary.
_AUTHORITY_KEEPALIVE_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_authority_keepalive_active", default=False
)

_CALLER_AUTHORITY_FIELDS = frozenset({"_actor", "_roles", "_tenant"})


def _reject_caller_authority(kwargs: dict[str, Any]) -> None:
    """Reject legacy tool fields that attempted to self-assert authority."""
    if _CALLER_AUTHORITY_FIELDS.intersection(kwargs):
        raise PermissionError("Caller-supplied graph authority is forbidden")


class UnsupportedToolFieldError(ValueError):
    """U-74: a caller (generically the REST twin, which forwards the raw JSON
    body as kwargs with no schema validation) supplied a field the target
    tool's signature does not accept.

    ``POST /engine/tenants`` with ``{"action": "list", "connection":
    "default"}`` used to reach ``_engine_domain_tool(action, params_json,
    graph)`` as an unfiltered ``**body`` call, raise a raw ``TypeError:
    _engine_domain_tool() got an unexpected keyword argument 'connection'``,
    and surface as an opaque HTTP 500 — indistinguishable from a real server
    fault. This is a distinct exception type (rather than reusing the
    existing ``_missing_required`` ``ValueError``) so the generic REST
    endpoint factory below can map it to a deterministic 4xx instead of the
    default 500, without changing behavior for any other error class."""


def _validate_tool_kwargs_against_signature(
    tool_name: str, tool_func: Any, kwargs: dict
) -> None:
    """Fail closed on a field the tool's own signature does not declare,
    instead of forwarding it into the call and letting a bare ``TypeError``
    surface as an internal error (U-74). A tool that declares ``**kwargs`` is
    exempted — it explicitly accepts arbitrary fields."""
    import inspect

    try:
        parameters = inspect.signature(tool_func).parameters
    except (TypeError, ValueError):  # noqa: BLE001 — signature introspection
        # failing (e.g. a builtin/C-implemented callable with no inspectable
        # signature) is not this guard's problem to solve: this validation is
        # purely an ADDITIONAL fail-fast check layered in front of the real
        # call, so skipping it here only forgoes the nicer 4xx and falls back
        # to today's behavior (any real failure still surfaces normally from
        # the call itself, e.g. as the existing bare-TypeError-to-500 path).
        return
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return
    unknown = sorted(set(kwargs) - set(parameters))
    if unknown:
        raise UnsupportedToolFieldError(
            f"Tool {tool_name!r} does not accept field(s): {', '.join(unknown)}."
        )


def _resolve_verified_scope_actor(session):
    """Resolve the ambient actor for `verified_tool_session_scope`, if any.

    Returns ``None`` when no actor is bound. Raises when a bound,
    authenticated actor disagrees with the session's own actor.
    """
    from agent_utilities.security.brain_context import (
        IdentityRequiredError,
        current_actor,
    )

    try:
        actor = current_actor()
    except IdentityRequiredError:
        return None
    if actor is not None and actor.authenticated and actor != session.actor:
        raise PermissionError("Verified actor and GraphSession authority differ")
    return actor


@contextlib.contextmanager
def verified_tool_session_scope():
    """Scope one served tool call to middleware/process-minted authority.

    Identity, tenant, audience, and policy revision are validated before tool
    dispatch. The error surface deliberately omits principal, tenant, token,
    endpoint, and policy values.
    """
    from agent_utilities.api.session import (
        current_session,
        use_session,
    )
    from agent_utilities.security.brain_context import use_actor

    ambient = current_session()
    session = ambient or _PROCESS_SESSION
    if session is None:
        raise PermissionError("Verified GraphSession required")
    try:
        session.engine_verified_context()
    except PermissionError:
        raise PermissionError("Verified GraphSession authority is incomplete") from None

    actor = _resolve_verified_scope_actor(session)

    with contextlib.ExitStack() as stack:
        if ambient is None:
            stack.enter_context(use_session(session))
        if actor is None or actor != session.actor:
            stack.enter_context(use_actor(session.actor))
        yield session


async def _execute_tool(tool_name: str, **kwargs) -> Any:
    tool_func = REGISTERED_TOOLS.get(tool_name)
    if not tool_func:
        raise ValueError(f"Tool {tool_name} not registered")

    import inspect

    _reject_caller_authority(kwargs)
    # U-74: fail closed on a field the tool's signature does not declare
    # (e.g. a caller reusing the query/write `connection` selector against a
    # lifecycle tool that is action-only) BEFORE invocation, rather than
    # forwarding it and letting a raw TypeError surface as an opaque 500.
    _validate_tool_kwargs_against_signature(tool_name, tool_func, kwargs)

    # Tool functions declare params as ``name: T = Field(default=...)``. When the tool is
    # invoked through FastMCP, the schema layer resolves those defaults. Calling the raw
    # function directly here (internal callers, the REST gateway, tests) does NOT — so any
    # omitted param would be bound to the raw ``FieldInfo`` object, later blowing up with
    # "'FieldInfo' object has no attribute 'replace'" / "not JSON serializable". Resolve
    # FieldInfo defaults for omitted params so direct invocation matches the MCP behavior.
    _missing_required: list[str] = []
    try:
        from pydantic.fields import FieldInfo
        from pydantic_core import PydanticUndefined

        for _name, _param in inspect.signature(tool_func).parameters.items():
            if _name in kwargs:
                continue
            _default = _param.default
            if isinstance(_default, FieldInfo):
                _resolved = _default.default
                if _resolved is not PydanticUndefined:
                    kwargs[_name] = _resolved
                elif getattr(_default, "default_factory", None) is not None:
                    kwargs[_name] = _default.default_factory()  # type: ignore[misc, call-arg]
                else:
                    # Required param (Field with no default) omitted: without this the
                    # raw FieldInfo would bind and later blow up deep in the tool with a
                    # cryptic "'FieldInfo' object has no attribute 'strip'". Fail loud
                    # with the actual missing-arg name instead.
                    _missing_required.append(_name)
    except Exception as exc:
        logger.warning("tool default resolution failed: %s", exc)
    if _missing_required:
        raise ValueError(
            f"Tool {tool_name!r} missing required argument(s): "
            f"{', '.join(_missing_required)}."
        )

    import asyncio

    # Dispatch isolation (CONCEPT:AU-ECO.mcp.gateway-dispatch-isolation): most graph_*/
    # engine_* tools are SYNC and do blocking engine I/O. Running them inline blocks the ONE
    # gateway asyncio loop, so a single hung/misbehaving tool call (an uncompiled engine
    # surface, a bad action, a wedged backend) freezes the whole graph-os child and
    # disconnects EVERY connected MCP client. Run sync tools on a worker thread and bound
    # every call with a timeout so a hung tool FAILS LOUD and frees the loop instead of taking
    # the gateway down. The timeout is > the delegation wall-clock so execute_agent isn't
    # killed. Threads propagate the current contextvars (actor/session) via to_thread.
    _TOOL_CALL_TIMEOUT_S = 320.0

    # Dispatch isolation (CONCEPT:AU-ECO.mcp.gateway-dispatch-isolation): most graph_*/
    # engine_* tools are SYNC and do blocking engine I/O. Running them inline blocks the ONE
    # gateway asyncio loop, so a single hung/misbehaving tool call (an uncompiled engine
    # surface, a bad action, a wedged backend) freezes the whole graph-os child and
    # disconnects EVERY connected MCP client. Run sync tools on a worker thread and bound
    # every call with a timeout so a hung tool FAILS LOUD and frees the loop instead of taking
    # the gateway down. The timeout is > the delegation wall-clock so execute_agent isn't
    # killed. Threads propagate the current contextvars (actor/session) via to_thread.
    _TOOL_CALL_TIMEOUT_S = 320.0

    async def _run() -> Any:
        if inspect.iscoroutinefunction(tool_func):
            return await asyncio.wait_for(
                tool_func(**kwargs), timeout=_TOOL_CALL_TIMEOUT_S
            )
        return await asyncio.wait_for(
            asyncio.to_thread(tool_func, **kwargs), timeout=_TOOL_CALL_TIMEOUT_S
        )

    async def _guarded() -> Any:
        try:
            active_session = await _ensure_process_authority_current()
            with verified_tool_session_scope():
                # D-SNV-5: a dispatch may run for the whole _TOOL_CALL_TIMEOUT_S
                # window, but authority is otherwise checked only once, at entry.
                # authority_keepalive_scope gives a renewable (server-minted
                # process/client-credentials) session a background keepalive for
                # the dispatch's duration so a long delegation renews its own
                # authority instead of failing closed mid-flight — the SAME
                # primitive Orchestrator.execute_agent opens directly, so a
                # nested execute_agent call here (a tool that itself delegates)
                # does not start a second renewal loop. A caller-presented
                # bearer JWT has no credential_lease and is never proactively
                # renewed here — that would be forging authority the server
                # does not hold.
                async with authority_keepalive_scope(active_session):
                    return await _run()
        except TimeoutError:
            return {
                "error": (
                    f"tool {tool_name!r} exceeded the {_TOOL_CALL_TIMEOUT_S:.0f}s dispatch "
                    "timeout and was abandoned; the gateway stayed responsive (fail-loud "
                    "dispatch isolation)."
                ),
                "tool": tool_name,
                "degraded": True,
            }

    # CONCEPT:AU-ORCH.scheduling.resource-priority-edict — an MCP tool call is the
    # INTERACTIVE entry boundary (a live Claude / end-user request). Tag the whole
    # dispatch INTERACTIVE so its engine reads — including a delegation's RAG
    # context-compilation GetNodeProperties point-reads, which run on the ``to_thread``
    # worker that inherits this context — carry the top QoS class and claim the engine's
    # reserved read lane ahead of a saturating background-ingestion write storm. Tag ONLY
    # when the context is UNTAGGED: a re-entrant call from a delegated agent (ORCHESTRATION)
    # or a background task (BACKGROUND_INGESTION) keeps its own, lower class — never upgraded.
    from agent_utilities.core.resource_priority import (
        PriorityClass,
        current_priority,
        priority_scope,
    )

    if current_priority() is None:
        with priority_scope(PriorityClass.INTERACTIVE):
            return await _guarded()
    return await _guarded()


def build_native_graphos_toolset(tool_names: list[str], *, toolset_id: str) -> Any:
    """Bind registered GraphOS tools for one governed in-process delegation.

    Native delegation must not connect GraphOS back to its own HTTP endpoint or
    call raw registered functions directly.  Each generated PydanticAI tool
    preserves the registered function's schema but dispatches through
    :func:`_execute_tool`, which reuses the verified caller session, rejects
    caller-supplied authority, resolves FastMCP defaults, and preserves bounded
    dispatch isolation.  The marker is consumed by the mandatory identity-policy
    wrapper before a specialist receives the toolset.
    """

    from pydantic_ai import Tool
    from pydantic_ai.toolsets.function import FunctionToolset

    if not tool_names or len(tool_names) != len(set(tool_names)):
        raise ValueError("native GraphOS tool names must be non-empty and unique")
    if not toolset_id or len(toolset_id) > 128:
        raise ValueError("native GraphOS toolset id is invalid")

    registered: list[tuple[str, Any]] = []
    for name in tool_names:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name or "") is None:
            raise ValueError("native GraphOS tool name is invalid")
        function = REGISTERED_TOOLS.get(name)
        if function is None:
            raise RuntimeError("requested native GraphOS tool is unavailable")
        registered.append((name, function))

    tools: list[Any] = []
    for name, function in registered:
        schema_source = Tool(function, name=name)

        async def dispatch(_tool_name: str = name, **kwargs: Any) -> Any:
            return await _execute_tool(_tool_name, **kwargs)

        tools.append(
            Tool.from_schema(
                dispatch,
                name=name,
                description=schema_source.description,
                json_schema=schema_source.function_schema.json_schema,
                sequential=schema_source.sequential,
            )
        )

    return FunctionToolset(
        tools,
        id=toolset_id,
        metadata={"graphos_native": True},
    )


def safe_json_load(s: Any) -> Any:
    if hasattr(s, "model_dump"):
        return s.model_dump()
    if isinstance(s, str):
        try:
            return json.loads(s)
        except Exception as exc:  # noqa: BLE001 — non-JSON string is a normal input, not a failure
            logger.debug(
                "safe_json_load: input is not JSON, returned as-is: %s",
                type(exc).__name__,
            )
    return s


from agent_utilities.security.error_surface import public_error_payload
from starlette.requests import Request
from starlette.responses import JSONResponse


def _external_failure_payload(
    exc: BaseException, *, code: str = "operation_failed"
) -> dict[str, str]:
    """Return a correlation-safe public error without exception details.

    Driver and tool exceptions routinely embed credentials, endpoints, local
    paths, queries, or request payloads.  External surfaces receive only a
    stable code/message and an opaque correlation identifier.  The matching
    log entry deliberately records the exception *type* only.
    """

    return public_error_payload(exc, logger=logger, code=code)


def _external_error_response(
    exc: BaseException, *, status_code: int = 500, code: str = "operation_failed"
) -> JSONResponse:
    """Build the canonical exception-safe REST error response."""

    return JSONResponse(
        _external_failure_payload(exc, code=code), status_code=status_code
    )


# ── Canonical tool ⇄ REST parity map ────────────────────────────────────────
# Single source of truth: every action-routed MCP tool in ``REGISTERED_TOOLS``
# has exactly one collapsed action-routed REST twin (POST, JSON body carries the
# ``action`` and its args). Granular CRUD sub-routes (``/graph/write/node`` etc.)
# are layered on top for fine-grained HTTP clients, but this map guarantees that
# anything callable over MCP is also callable over REST and vice versa. The
# parity contract test (tests/unit/test_gateway_mcp_parity.py) asserts this map
# stays in lockstep with REGISTERED_TOOLS so the two surfaces never drift.
ACTION_TOOL_ROUTES: dict[str, str] = {
    "browser_control": "/browser/control",
    "graph_a2a": "/graph/a2a",
}

# Immutable seed used by deterministic catalog generators. Runtime registrars
# extend ``ACTION_TOOL_ROUTES`` with their own twins, but a generator must never
# inherit routes left behind by an earlier server build in the same process.
BASE_ACTION_TOOL_ROUTES = MappingProxyType(dict(ACTION_TOOL_ROUTES))


def _is_engine_dispatch_client_error(parsed: Any) -> bool:
    """U-74 (GOC-83-W05): does a parsed ``engine_<domain>`` dispatch result
    represent a CALLER-caused parameter mistake, rather than a real result or
    a genuine server-side/engine-side failure?

    ``engine_tools._dispatch`` is the ONE dispatcher every ``engine_<domain>``
    tool shares; it never raises for an unknown action name, an unknown/
    duplicate/wrong-type/missing parameter to the target EG method, or a
    malformed ``params_json`` — it catches all of those and returns a JSON
    STRING error payload instead, by design, so an MCP tool caller always gets
    data back (never an exception it has to unwrap). That is the right
    contract for the MCP surface, but the generic REST twin
    (:func:`_make_tool_endpoint`) unconditionally wrapped ANY such string in
    ``{"status": "success", ...}`` at HTTP 200 — a client-caused parameter
    mistake was indistinguishable, over REST, from a real result. This
    recognizes the two client-error shapes ``_dispatch`` emits (an unknown/
    non-callable action name — a bare ``error: str``; or an
    ``error.code == "invalid_request"`` structured payload from
    ``public_error_json`` — unknown/duplicate/wrong-type/missing parameter, or
    an undecodable ``params_json``), scoped ONLY to ``engine_*`` REST
    responses (checked by the caller) so no other tool's REST status-code
    contract changes. A ``dependency_unavailable`` (engine unreachable) or
    unclassified ``operation_failed`` payload is NOT a client mistake and is
    deliberately left at 200, matching prior behavior exactly.
    """
    if not isinstance(parsed, dict):
        return False
    error = parsed.get("error")
    if isinstance(error, str):
        # `_dispatch`'s two pre-call, action-name-level rejections: {"error":
        # "unknown action ...", "actions": [...]} / {"error": "engine_<domain>
        # has no callable action ..."} — both are a caller supplying an action
        # name the domain doesn't have, i.e. exactly as client-caused as an
        # unsupported top-level field.
        return True
    if isinstance(error, dict) and error.get("code") == "invalid_request":
        # `public_error_json(..., code="invalid_request")` — raised for an
        # undecodable `params_json`, or a `TypeError` from calling the target
        # EG method with an unknown/duplicate/wrong-type/missing argument.
        return True
    return False


def _make_tool_endpoint(tool_name: str):
    """Build a thin REST handler that dispatches a JSON body to an MCP tool.

    Both the MCP tool surface and the REST surface funnel through
    :func:`_execute_tool` against the shared in-process engine, so a handler is
    just: parse body → execute tool → wrap result. This factory is the canonical
    adapter; per-tool endpoints below that need bespoke parsing keep their own
    definitions, but every tool in :data:`ACTION_TOOL_ROUTES` without one is
    served by this.
    """

    is_engine_domain_tool = tool_name.startswith("engine_")

    async def _handler(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            res = await _execute_tool(tool_name, **body)
            parsed = safe_json_load(res)
            # U-74 (GOC-83-W05): an `engine_<domain>` action-name or
            # parameter mistake never raises (see `_is_engine_dispatch_
            # client_error`'s docstring) — surface it as a deterministic 4xx
            # instead of an HTTP 200 that hides a caller-caused failure behind
            # `"status": "success"`. Scoped to `engine_*` tools only; every
            # other tool's REST status-code contract is unchanged.
            if is_engine_domain_tool and _is_engine_dispatch_client_error(parsed):
                return JSONResponse(
                    {"status": "failed", "result": parsed}, status_code=400
                )
            if (
                tool_name == "graph_sessions"
                and isinstance(parsed, dict)
                and isinstance(parsed.get("evidence"), dict)
                and parsed["evidence"].get("ready") is False
            ):
                # Fleet health/topology are supervisory evidence, not ordinary
                # session CRUD. Preserve the shared fail-closed HTTP signal
                # while returning the exact typed evidence body used by MCP.
                return JSONResponse(
                    {"status": "unavailable", "result": parsed}, status_code=503
                )
            return JSONResponse({"status": "success", "result": parsed})
        except UnsupportedToolFieldError as e:
            # U-74: a caller-supplied field the tool doesn't accept is a
            # client-side schema mismatch, not a server fault — deterministic
            # 4xx instead of the generic 500 every other exception maps to.
            return _external_error_response(e, status_code=400, code="invalid_request")
        except Exception as e:
            return _external_error_response(e)

    _handler.__name__ = f"{tool_name}_endpoint"
    return _handler


# Default agent identity for provenance tracking
_AGENT_ID = setting("AGENT_ID", f"mcp-client-{uuid.uuid4().hex}")
_SESSION_ID = setting("SESSION_ID", uuid.uuid4().hex)


_ENGINE_LOCK = threading.Lock()


_ensure_process_authority_current = cast(Any, None)
_get_engine = cast(Any, None)
graph_client = cast(Any, None)
_drain_engine_transport = cast(Any, None)
_mint_process_session = cast(Any, None)
_release_readiness_authority = cast(Any, None)
_set_readiness_authority = cast(Any, None)
_start_engine_bootstrap = cast(Any, None)
_start_process_authority_supervisor = cast(Any, None)
_stop_process_authority_supervisor = cast(Any, None)
authority_keepalive_scope = cast(Any, None)
set_process_session = cast(Any, None)

import graph_os.mcp_server.bootstrap as _bootstrap

for _bootstrap_name in (
    "_AGENT_ID",
    "_AUTHORITY_KEEPALIVE_ACTIVE",
    "_ENGINE_LOCK",
    "_PROCESS_AUTHORITY_STOP",
    "_PROCESS_AUTHORITY_THREAD",
    "_PROCESS_SESSION",
    "_PROCESS_SESSION_REFRESH_LOCK",
    "_SESSION_ID",
    "setting",
):
    # ``bootstrap`` declares these names as explicit cycle-breaking host slots.
    # Assign them rather than using ``setdefault``: the declarations exist with
    # a ``None`` sentinel by design, so setdefault would retain that sentinel
    # and make the first native dispatch fail before reaching a tool.
    _bootstrap.__dict__[_bootstrap_name] = globals()[_bootstrap_name]
for _bootstrap_name in _bootstrap.BOOTSTRAP_EXPORTS:
    globals()[_bootstrap_name] = getattr(_bootstrap, _bootstrap_name)


def _build_server(bootstrap: bool = True):
    """Build the KG MCP server with all tools registered.

    Args:
        bootstrap: Whether this is a directly served process. The caller starts
            background engine bootstrap only after process identity is minted.
            The API gateway calls this with ``bootstrap=False`` (via
            :func:`ensure_tools_registered`) because it owns the engine/daemon
            lifecycle itself and only needs ``REGISTERED_TOOLS`` populated so the
            centralized REST handlers can dispatch.
    """
    from agent_connector_sdk.mcp.server import create_mcp_server

    from graph_os.mcp_server.serving import (
        VerifiedSessionMiddleware,
        register_metrics_route,
    )

    # In embedded mode (bootstrap=False, e.g. the API gateway populating
    # REGISTERED_TOOLS) do NOT parse the host process's argv — pass an empty
    # command line so the factory uses defaults instead of choking on unrelated
    # flags (pytest/uvicorn args) with SystemExit.
    args, mcp, _sdk_middleware = create_mcp_server(
        "graph-os",
        version=__version__,
        instructions=(
            "Knowledge Graph MCP Server for agent-utilities. "
            "Provides access to the shared unified Knowledge Graph that powers "
            "the 5-pillar agent architecture (ORCH, KG, AHE, ECO, OS). "
            "Use kg_query for Cypher queries, kg_search for semantic search, "
            "kg_analyze for LLM-powered cross-reference analysis, "
            "and kg_ingest_* for adding data.\n\n"
            "graph-os is ALSO the MCP fleet gateway: its own KG/engine tools are "
            "always on, and it can load ANY other MCP server (declared in "
            "epistemic-graph fleet catalog) ON DEMAND. Hundreds more tools across dozens of "
            "servers exist but are NOT loaded yet — so when you need a capability "
            "you don't see, do NOT assume it's unavailable; use the fleet meta-tools:\n"
            "  • find_tools(query) — semantic search for the right tool by intent\n"
            "  • list_catalog() — browse every mountable server and its tools\n"
            "  • load_tools(tools=[...] or servers=[...]) — mount them; they become "
            "directly callable immediately (the tool list updates live)\n"
            "  • unload_tools(...) — retract tools to reclaim context\n"
            "  • multiplexer_status — health of mounted children\n"
            "Always discover (find_tools/list_catalog) before concluding a tool "
            "doesn't exist.\n\n"
            "EXCEPTION — the always-load set (MCP_ALWAYS_LOAD / "
            "MCP_ALWAYS_LOAD_TOOLS): a short operator-chosen list of core servers "
            "and individual tools is mounted EAGERLY on your first request, so it "
            "is already in your tool list and needs no find_tools/load_tools hop. "
            "Its absence is therefore meaningful — if an always-load tool is NOT "
            "listed, that server is genuinely degraded (eager mounting fails soft), "
            "not merely undiscovered; multiplexer_status says which and why. "
            "Everything OUTSIDE that set still follows the discover-first rule "
            "above. Inspect or change the set with "
            "graph_config(action='get'/'describe'/'set', key='MCP_ALWAYS_LOAD')."
        ),
        command_args=None if bootstrap else [],
        transport_choices=("stdio", "streamable-http"),
    )
    # The SDK factory supplies the privacy-safe error and per-caller rate-limit
    # middleware; GraphOS binds the verified caller session inside them.
    middlewares = [
        *_sdk_middleware,
        VerifiedSessionMiddleware(lambda: _PROCESS_SESSION),
    ]
    register_metrics_route(
        mcp,
        transport=str(getattr(args, "transport", "stdio")),
        host=str(getattr(args, "host", "")),
    )

    # The SDK factory serves ``/health`` liveness. Readiness runs the truthful
    # bounded collector but answers only ready/not_ready, because the route is
    # unauthenticated for kubelet; component detail stays behind
    # authenticated surfaces.
    @mcp.custom_route("/health/ready", methods=["GET"])
    async def readiness_check(request: Request) -> JSONResponse:  # noqa: ARG001
        from agent_utilities.observability.runtime_health import (
            collect_health_async,
            is_overall_healthy,
        )

        report = await collect_health_async()
        ready = is_overall_healthy(report)
        return JSONResponse(
            {"status": "ready" if ready else "not_ready"},
            status_code=200 if ready else 503,
            headers={"Cache-Control": "no-store"},
        )

    # ARD registry surface (CONCEPT:AU-ECO.mcp.eco-serves-two-ard/ECO-4.97) — the graph-os twin of the
    # gateway routes in server/routers/ard.py. This is the container the deploy
    # mechanic restarts, so it must answer the well-known + search paths too. Both
    # delegate into the same ecosystem.ard_* core to stay in lockstep with the gateway.
    @mcp.custom_route("/.well-known/ai-catalog.json", methods=["GET"])
    async def ard_ai_catalog(request: Request) -> JSONResponse:  # noqa: ARG001
        from agent_utilities.ecosystem.ard_registry import build_ai_catalog

        return JSONResponse(build_ai_catalog())

    @mcp.custom_route("/search", methods=["POST"])
    async def ard_search_route(request: Request) -> JSONResponse:
        from agent_utilities.ecosystem.ard_federation import ArdFederationRelay

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — malformed body ⇒ empty query, not a 500
            body = {}
        query = body.get("query") or {}
        text = str(query.get("text") or body.get("text") or "")
        types = ((query.get("filter") or {}).get("type")) or None
        page_size = int(body.get("pageSize") or 5)
        result = ArdFederationRelay().federated_search(
            text,
            types=types,
            page_size=page_size,
            mode=body.get("federationMode"),
            via=body.get("via") or [],
        )
        return JSONResponse(result)

    from graph_os.a2a.mcp import register_a2a_tools
    from graph_os.browser_control.mcp import register_browser_control_tools

    register_browser_control_tools(mcp)
    register_a2a_tools(mcp)

    return args, mcp, middlewares


def ensure_tools_registered() -> None:
    """Idempotently register all ``graph_*`` tools into ``REGISTERED_TOOLS``.

    The centralized REST handlers (and the API gateway that mounts them via
    :func:`_mount_rest_routes`) dispatch through ``REGISTERED_TOOLS`` using
    :func:`_execute_tool`. Building the MCP server populates that dict as a side
    effect; we discard the throwaway FastMCP instance and skip the engine
    bootstrap (``bootstrap=False``) because the gateway owns the engine/daemon
    lifecycle and the handlers resolve the engine lazily via ``_get_engine()``.
    """
    if REGISTERED_TOOLS:
        return
    _build_server(bootstrap=False)
