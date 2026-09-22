#!/usr/bin/python
"""Knowledge Graph MCP Server — Thin wrapper over IntelligenceGraphEngine.

CONCEPT:AU-ECO.mcp.knowledge-graph-exposure — Knowledge Graph MCP Exposure

Exposes the internal Knowledge Graph as MCP tools for external agents
(Claude Code, Antigravity IDE, OpenCode, Devin) to query, search, and
ingest data into the shared unified KG.

Architecture:
    This module reuses the existing ``create_mcp_server()`` infrastructure
    from ``agent_utilities.mcp.server_factory`` — zero new abstractions.
    All tools delegate to ``IntelligenceGraphEngine`` methods that already
    exist in the 15-phase pipeline.

Security:
    - Read-only by default for external agents.
    - Write access requires ``kg:write`` scope via MCP auth.
    - Every write carries provenance: ``agent_id``, ``session_id``,
      ``workspace_path`` for multi-agent traceability.

Usage:
    # Start as stdio MCP server (default):
    graph-os --transport stdio

    # Start as HTTP transport:
    graph-os --transport streamable-http --host 127.0.0.1 --port 8004

Cross-IDE Discovery:
    Register the Graph OS endpoint with the MCP client::

        {
          "mcpServers": {
            "graph-os": {
              "command": "graph-os",
              "args": ["--transport", "stdio"]
            }
          }
        }
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any, cast

from agent_utilities.core.config import setting

from graph_os._version import __version__

logger = logging.getLogger(__name__)


REGISTERED_TOOLS: dict[str, Any] = {}


def _build_dummy_request(path_params=None, json_body=None):
    from starlette.requests import Request

    scope: dict[str, Any] = {
        "type": "http",
        "path_params": path_params or {},
        "query_string": b"",
        "headers": [],
    }
    req = Request(scope)
    if json_body is not None:

        async def mock_json():
            return json_body

        # Intentional instance-level override of Request.json for this dummy/mock
        # request (there is no other way to fake a request body without a real ASGI
        # receive channel) — not a real Request whose .json() must stay bound.
        req.json = mock_json
    return req


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


#: The ``graph_query`` MCP tool's own documented parameters (KG-2.134 /
#: ``agent_utilities/mcp/tools/query_tools.py``'s ``graph_query`` signature).
#: Kept as an explicit allowlist so this REST twin never blind-splats an
#: arbitrary request body into ``_execute_tool`` (LANE 9 / U-74 follow-up):
#: an unrecognized field becomes an immediate, clean 4xx here instead of
#: reaching ``_execute_tool`` at all.
_GRAPH_QUERY_TOOL_FIELDS = frozenset(
    {
        "as_of",
        "connection",
        "query",
        "graph",
        "include_epistemic",
        "params",
        "reference_id",
        "scope",
    }
)


@contextlib.contextmanager
def _rest_tabular_delegated_identity(request: Request):
    """Bridge one already-verified REST bearer into the tabular delegation port.

    The gateway's outer ``ActorIdentityMiddleware`` validates the bearer and
    binds its actor before this route runs. Duplicate/malformed headers fail
    through the canonical parser, and ContextVars are always reset after
    dispatch.
    """

    from agent_utilities.mcp.delegated_auth import (
        _reset_delegated_identity,
        _set_delegated_identity,
    )
    from agent_utilities.security.auth import parse_bearer_authorization
    from agent_utilities.security.brain_context import current_actor

    actor = current_actor()
    actor.ensure_credential_current()
    if not actor.authenticated:
        raise PermissionError("verified Trino caller identity is required")
    authorization = [
        value
        for key, value in request.scope.get("headers", ())
        if isinstance(key, bytes) and key.lower() == b"authorization"
    ]
    token = parse_bearer_authorization(authorization)
    if token is None:
        raise PermissionError("verified Trino bearer credential is required")
    context_tokens = _set_delegated_identity(token, {})
    try:
        yield
    finally:
        _reset_delegated_identity(context_tokens)


async def graph_query_endpoint(request: Request) -> JSONResponse:
    """REST twin of the ``graph_query`` MCP tool's canonical ``query`` field."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse(
            {"status": "error", "message": "request body must be a JSON object"},
            status_code=400,
        )

    result = _graph_query_request_kwargs(body)
    if not isinstance(result, dict):
        payload, status_code = result
        return JSONResponse(payload, status_code=status_code)
    kwargs = result

    try:
        res = await _execute_tool("graph_query", **kwargs)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except UnsupportedToolFieldError as e:
        # Defense-in-depth: `_GRAPH_QUERY_TOOL_FIELDS` is kept in sync with
        # the tool's real signature above, so this should be unreachable —
        # but if it ever drifts, still surface the client-caused 4xx rather
        # than the generic 500 below (U-74).
        return _external_error_response(e, status_code=400, code="invalid_request")
    except Exception as e:
        return _external_error_response(e)


def _graph_query_request_kwargs(
    body: dict[str, Any],
) -> dict[str, Any] | tuple[dict[str, Any], int]:
    """Validate a ``graph_query`` REST body into canonical tool kwargs."""
    unknown = sorted(set(body) - _GRAPH_QUERY_TOOL_FIELDS)
    if unknown:
        return (
            {
                "status": "error",
                "message": f"Unsupported field(s): {', '.join(unknown)}.",
            },
            400,
        )

    return {k: v for k, v in body.items() if k in _GRAPH_QUERY_TOOL_FIELDS}


async def tabular_query_endpoint(request: Request) -> JSONResponse:
    """REST twin of the dedicated ``tabular_query`` MCP operation."""

    body = _strict_json_object(
        await _read_json_body(request), allowed_fields=frozenset({"sql"})
    )
    if isinstance(body, JSONResponse):
        return body
    try:
        with _rest_tabular_delegated_identity(request):
            result = await _execute_tool("tabular_query", **body)
        return JSONResponse({"status": "success", "result": safe_json_load(result)})
    except UnsupportedToolFieldError as exc:
        return _external_error_response(exc, status_code=400, code="invalid_request")
    except Exception as exc:
        return _external_error_response(exc)


async def graph_search_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool("graph_search", **body)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except UnsupportedToolFieldError as e:
        # U-74: same deterministic-4xx treatment as `_make_tool_endpoint` —
        # this hand-written endpoint predates that factory and was never
        # updated to catch this exception subclass specially, so a caller
        # field the `graph_search` tool doesn't accept fell through to the
        # generic 500 below instead. `graph_search`'s own wire field names
        # (`query`, `mode`, `top_k`, ...) already match its documented tool
        # parameters 1:1 — see `graph_search`'s signature in
        # `agent_utilities/mcp/tools/query_tools.py` — so unlike
        # the canonical `graph_query` field there is no latent mismatch; only
        # the missing status-code mapping needed fixing.
        return _external_error_response(e, status_code=400, code="invalid_request")
    except Exception as e:
        return _external_error_response(e)


async def graph_write_endpoint(request: Request) -> JSONResponse:
    """POST /graph/write — collapsed, typed dispatch for every ``graph_write``
    action. Covers the six actions that used to have their own granular
    routes (``add_node``, ``add_edge``, ``delete_edge``, ``bulk_ingest``,
    ``log_chat``, ``register_execution`` — formerly
    ``/graph/write/{node,edge,bulk,chat,execution}``) plus every other
    action the tool accepts (``delete_node``, ``register_external_graph``,
    ``compare_and_set``, ``store_memory``, ``recall_memory``,
    ``recall_media``, ``submit_sdd``, ``check_loop``) — see
    ``GraphWriteAction`` below for the full discriminated union. The body is
    validated against that union instead of forwarded blind (``**body``), so
    an unrecognized/malformed ``action`` is a clean 400, never a 500, and
    FastAPI documents every action's real shape (mounted via
    ``add_api_route(..., response_model=GraphToolResponse)`` in
    ``_mount_rest_routes``, not the raw Starlette ``add_route`` most other
    handlers in this file still use).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        action_model = _GRAPH_WRITE_ACTION_ADAPTER.validate_python(body)
    except ValidationError as e:
        return _external_error_response(e, status_code=400, code="invalid_request")
    try:
        res = await _dispatch_graph_write_action(action_model)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except UnsupportedToolFieldError as e:
        # U-74, same class of fix as `graph_search_endpoint` above.
        return _external_error_response(e, status_code=400, code="invalid_request")
    except Exception as e:
        return _external_error_response(e)


graph_ingest_endpoint = _make_tool_endpoint("graph_ingest")


graph_analyze_endpoint = _make_tool_endpoint("graph_analyze")


#: The graph_mine actions with a natural-body REST twin (CONCEPT:EG-KG.mining.frequent-itemset-mining).
#: Each mounts ``POST /api/mining/<action>`` dispatching the SAME
#: ``_execute_tool("graph_mine", action=...)`` core as the MCP verb — surface
#: parity is a build gate, so the MCP action + its REST twin ship together.
MINING_ACTIONS = (
    "associate",
    "cluster",
    "anomaly",
    "classify_fit",
    "classify_predict",
    "reduce",
    "sequence",
    "forecast",
    "text",
    "subgraph",
    "entity_resolve",
    "causal_impact",
    "process",
    "root_cause",
    "risk_propagation",
    "ontology_gap",
    "retrieval_quality",
    "community",
)


#: The graph_learn actions with a natural-body REST twin (CONCEPT:EG-KG.graphlearn.link-predictor).
#: Each mounts ``POST /api/graphlearn/<action>`` dispatching the SAME
#: ``_execute_tool("graph_learn", action=...)`` core as the MCP verb — surface parity.
GRAPHLEARN_ACTIONS = ("fit", "predict")


#: The graph_mine_deep actions with a natural-body REST twin (CONCEPT:AU-KG.mining.dsm-forecast-delegation —
#: Phase-6 heavy-dep delegation to data-science-mcp). Each mounts
#: ``POST /api/mining/deep/<action>`` dispatching the SAME
#: ``_execute_tool("graph_mine_deep", action=...)`` core as the MCP verb — surface parity.
DEEP_MINING_ACTIONS = (
    "deep_forecast",
    "deep_classify",
    "autoencoder_anomaly",
    "xgboost",
    "embed",
)


async def _read_json_body(request: Request) -> Any:
    """Read a REST body using the gateway's permissive JSON fallback."""

    try:
        return await request.json()
    except Exception:
        return {}


def _strict_json_object(
    body: Any, *, allowed_fields: frozenset[str]
) -> dict[str, Any] | JSONResponse:
    """Return an exact-field JSON object or its deterministic REST error."""

    if not isinstance(body, dict):
        return JSONResponse(
            {"status": "error", "message": "request body must be a JSON object"},
            status_code=400,
        )
    unknown = sorted(set(body) - allowed_fields)
    if unknown:
        return JSONResponse(
            {
                "status": "error",
                "message": f"Unsupported field(s): {', '.join(unknown)}.",
            },
            status_code=400,
        )
    return body


async def _run_json_endpoint(
    request: Request,
    tool_name: str,
    kwargs_factory: Callable[[Any], dict[str, Any]],
    *,
    require_object: bool = False,
) -> JSONResponse:
    """Dispatch a JSON REST adapter through the shared tool/error boundary."""

    body = await _read_json_body(request)
    if require_object and not isinstance(body, dict):
        return JSONResponse(
            {"status": "error", "message": "body must be a JSON object"},
            status_code=400,
        )
    try:
        res = await _execute_tool(tool_name, **kwargs_factory(body))
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as exc:
        return _external_error_response(exc)


def _action_body_kwargs(body: dict[str, Any], action: str) -> dict[str, Any]:
    """Build the natural-body payload used by action-routed REST adapters."""

    graph = body.pop("graph", "") or ""
    return {"action": action, "params_json": json.dumps(body), "graph": graph}


def _query_top_k_kwargs(body: Any, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "query": body.get("query", ""),
        "top_k": int(body.get("top_k", 10)),
    }


def _search_mode_top_k_kwargs(body: Any, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "query": body.get("query", ""),
        "top_k": int(body.get("top_k", 10)),
    }


def _change_coupling_kwargs(body: Any) -> dict[str, Any]:
    return {
        "action": "change_coupling",
        "target": body.get("repo", ""),
        "depth": int(body.get("min_support", 3)),
    }


def _target_query_kwargs(
    body: Any,
    *,
    action: str,
    target_field: str,
    query_field: str,
    target_default: str = "",
    query_default: str = "",
    top_k_default: int = 10,
) -> dict[str, Any]:
    return {
        "action": action,
        "target": body.get(target_field, target_default),
        "query": body.get(query_field, query_default),
        "top_k": int(body.get("top_k", top_k_default)),
    }


def _code_evolution_kwargs(body: Any) -> dict[str, Any]:
    return _target_query_kwargs(
        body,
        action="code_evolution",
        target_field="mode",
        query_field="target",
        target_default="file",
        top_k_default=20,
    )


def _context_kwargs(body: Any) -> dict[str, Any]:
    return _target_query_kwargs(
        body, action="context", target_field="target", query_field="query"
    )


def _code_context_kwargs(body: Any) -> dict[str, Any]:
    intent = str(body.get("intent", "how"))
    if body.get("cross_repo"):
        intent = f"{intent}+xrepo"
    return {
        "action": "code_context",
        "query": body.get("query", ""),
        "target": intent,
        "node_id": body.get("node_id", ""),
        "top_k": int(body.get("top_k", 10)),
        "depth": int(body.get("depth", 2)),
    }


def _explain_kwargs(body: Any) -> dict[str, Any]:
    domain = str(body.get("domain", ""))
    intent = str(body.get("intent", ""))
    return {
        "action": "explain",
        "query": body.get("query", ""),
        "target": f"{domain}:{intent}" if domain else intent,
        "node_id": body.get("node_id", ""),
        "top_k": int(body.get("top_k", 10)),
        "depth": int(body.get("depth", 2)),
    }


def _make_action_body_endpoint(tool_name: str, action: str):
    """Build a JSON-object action endpoint for a tool with natural parameters."""

    async def _endpoint(request: Request) -> JSONResponse:
        return await _run_json_endpoint(
            request,
            tool_name,
            lambda body: _action_body_kwargs(body, action),
            require_object=True,
        )

    return _endpoint


def _make_mining_deep_endpoint(action: str):
    """Build the REST twin for one ``graph_mine_deep`` action (CONCEPT:AU-KG.mining.dsm-forecast-delegation).

    ``POST /api/mining/deep/<action>`` accepts a natural body (``x``/``values``/
    ``source``, ``y``, ``writeback``, algo kwargs, ...) plus an optional ``graph``,
    and dispatches the SAME ``_execute_tool("graph_mine_deep", action=<action>, ...)``
    core the MCP verb uses — the delegated call to data-science-mcp and the KG
    foldback happen once, in that one core.
    """
    return _make_action_body_endpoint("graph_mine_deep", action)


def _make_graphlearn_endpoint(action: str):
    """Build the REST twin for one ``graph_learn`` action (CONCEPT:EG-KG.graphlearn.link-predictor).

    ``POST /api/graphlearn/<action>`` accepts a natural body (the action's kwargs,
    e.g. ``{node_label, direction, degree, epochs, writeback, ...}`` for fit,
    ``{model, node_label, top_k|candidate_pairs, writeback, ...}`` for predict) plus an
    optional ``graph``, and dispatches the SAME
    ``_execute_tool("graph_learn", action=<action>, ...)`` core as the MCP verb.
    """
    return _make_action_body_endpoint("graph_learn", action)


def _make_mining_endpoint(action: str):
    """Build the REST twin for one ``graph_mine`` action (CONCEPT:EG-KG.mining.frequent-itemset-mining).

    ``POST /api/mining/<action>`` accepts a natural mining body (the action's
    kwargs, e.g. ``{transactions|source,...}`` for associate, ``{features|source,
    algorithm,...}`` for cluster, ``{features|values|source,algorithm,...}`` for
    anomaly, ``{x|source,y,algorithm,...}`` for classify_fit, ``{model,x|source,...}``
    for classify_predict, ``{x|source,algorithm,n_components,...}`` for reduce,
    ``{sequences|source,min_support,algorithm,...}`` for sequence,
    ``{values,algorithm,horizon,...}`` for forecast,
    ``{docs|source,algorithm,k,...}`` for text,
    ``{label,min_support,max_edges,algorithm,...}`` for subgraph,
    ``{records|vectors|source,threshold,...}`` for entity_resolve,
    ``{series,control,intervention_index,...}`` for causal_impact,
    ``{traces,process_id,...}`` for process,
    ``{nodes,edges,scores,symptom,...}`` for root_cause,
    ``{nodes,edges,seed,...}`` for risk_propagation,
    ``{label,...}`` for ontology_gap,
    ``{traces,k,...}`` for retrieval_quality,
    ``{label,algorithm,...}`` for community) plus an
    optional ``graph``, and dispatches the SAME
    ``_execute_tool("graph_mine", action=<action>, ...)`` core as the MCP verb.
    """
    return _make_action_body_endpoint("graph_mine", action)


def _make_action_endpoint(tool_name: str):
    """Build an action-routed REST endpoint for a focused analyze-suite tool — the REST
    twin of the MCP tool, dispatching through the same ``_execute_tool`` core (KG-2.257)."""

    async def _endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            res = await _execute_tool(tool_name, **body)
            return JSONResponse({"status": "success", "result": safe_json_load(res)})
        except UnsupportedToolFieldError as e:
            # U-74, same class of fix as `graph_search_endpoint` above: this
            # factory blind-splats the body the same way, so any tool it
            # backs (graph_code/research/evaluate/explain/observe) shared the
            # missing 4xx mapping.
            return _external_error_response(e, status_code=400, code="invalid_request")
        except Exception as e:
            return _external_error_response(e)

    return _endpoint


graph_code_endpoint = _make_action_endpoint("graph_code")
graph_research_endpoint = _make_action_endpoint("graph_research")
graph_evaluate_endpoint = _make_action_endpoint("graph_evaluate")
graph_explain_endpoint = _make_action_endpoint("graph_explain")
graph_observe_endpoint = _make_action_endpoint("graph_observe")


async def graph_orchestrate_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool("graph_orchestrate", **body)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_configure_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool("graph_configure", **body)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


def _to_json_str(val: Any) -> str:
    if isinstance(val, dict | list):
        return json.dumps(val)
    return str(val) if val is not None else ""


# 1. Granular Graph Query endpoints
async def graph_query_federated_endpoint(request: Request) -> JSONResponse:
    body = _strict_json_object(
        await _read_json_body(request),
        allowed_fields=frozenset({"query", "params", "reference_id"}),
    )
    if isinstance(body, JSONResponse):
        return body
    try:
        res = await _execute_tool(
            "graph_query",
            query=body.get("query", ""),
            params=_to_json_str(body.get("params", {})),
            scope="federated",
            reference_id=body.get("reference_id", ""),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


# 2. Granular Graph Search endpoints
def _make_granular_search_endpoint(
    mode: str, *, include_top_k: bool = True
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Build one mode-fixed ``graph_search`` REST adapter.

    The per-mode URLs remain distinct, but all of them share the same JSON
    parsing, async dispatch, authority propagation, success envelope, and
    public error boundary. ``discover`` intentionally omits ``top_k`` because
    its historical adapter never forwarded that field to the tool.
    """

    def kwargs_factory(body: Any) -> dict[str, Any]:
        if include_top_k:
            return _search_mode_top_k_kwargs(body, mode)
        return {"query": body.get("query", ""), "mode": mode}

    async def _endpoint(request: Request) -> JSONResponse:
        return await _run_json_endpoint(request, "graph_search", kwargs_factory)

    endpoint_name = f"graph_search_{mode}_endpoint"
    _endpoint.__name__ = endpoint_name
    _endpoint.__qualname__ = endpoint_name
    _endpoint.__doc__ = (
        f"REST twin of graph_search mode={mode!r}; the route fixes the mode "
        "while preserving the underlying tool's request and response boundary."
    )
    return _endpoint


graph_search_concept_endpoint = _make_granular_search_endpoint("concept")
graph_search_analogy_endpoint = _make_granular_search_endpoint("analogy")
graph_search_memory_endpoint = _make_granular_search_endpoint("memory")
graph_search_discover_endpoint = _make_granular_search_endpoint(
    "discover", include_top_k=False
)
graph_search_dci_endpoint = _make_granular_search_endpoint("dci")


# 3. Collapsed Graph Write endpoint (POST + DELETE /graph/write)
#
# CONSOLIDATION: this used to be six separate granular routes — POST
# /graph/write/node, POST/DELETE /graph/write/edge, POST /graph/write/bulk,
# POST /graph/write/chat, POST /graph/write/execution — each a thin
# hand-written Starlette handler reading a handful of ``body.get`` keys.
# Collapsed into the SAME action-routed ``POST /graph/write`` the base
# endpoint already exposed (plus a ``DELETE /graph/write`` twin for
# ``delete_edge`` — see ``graph_write_delete_edge_endpoint`` below), now
# dispatched through a real Pydantic discriminated union
# (``GraphWriteAction``) instead of ``**body`` passthrough, so every action
# gets its own validated shape AND FastAPI documents it.
#
# The six "primary" variants below (``_AddNodeAction`` ..
# ``_RegisterExecutionAction``) extend the already-merged per-route models in
# ``graph_os.gateway.schemas.graph_ingest`` (imported, not redefined)
# — each adds the ``action`` discriminator plus ``connection``/``graph`` that
# the granular routes never forwarded. ``_BulkIngestAction`` additionally restores
# ``idempotency_key``/``evidence``/``upsert`` — the real defect this
# consolidation fixes: the deleted ``graph_write_bulk_endpoint`` forwarded
# ONLY ``nodes``, always taking the non-idempotent ``BatchUpdate``
# (``upsert=True``) path even when a caller supplied an idempotency key, so a
# retried bulk write could double-write.
#
# ``_OtherGraphWriteAction`` is the 7th union member and covers every action
# that never had its own granular route (``delete_node``,
# ``register_external_graph``, ``compare_and_set``, ``store_memory``,
# ``recall_memory``, ``recall_media``, ``submit_sdd``, ``check_loop``) — it
# IS ``GraphWriteRequest`` itself (imported, not redefined; the already
# merged, ``extra="allow"``, full-passthrough model), with ``action``
# narrowed to a Literal of exactly those eight values (Pydantic discriminated
# unions support several tag values mapping to one member). This preserves
# the pre-consolidation base route's full action vocabulary byte-for-byte
# instead of silently dropping it down to only the six actions collapsed
# here.
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from graph_os.gateway.schemas.graph_ingest import (
    GraphWriteBulkRequest,
    GraphWriteChatRequest,
    GraphWriteEdgeDeleteRequest,
    GraphWriteEdgeRequest,
    GraphWriteExecutionRequest,
    GraphWriteNodeRequest,
    GraphWriteRequest,
)

_CONNECTION_FIELD_DESCRIPTION = (
    "Named backend connection to write to (default = primary). Use a "
    "registered connection name, or 'all'/a comma-separated list to mirror "
    "the same write to several backends. Applies to every action (resolved "
    "generically before the action-specific dispatch) — not just "
    "bulk_ingest, which is all the granular routes ever exposed this on."
)
_GRAPH_FIELD_DESCRIPTION = (
    "Explicit physical engine graph to write to, independent of "
    "'connection'. Empty = the caller's own bound graph. Requires exactly "
    "one resolved 'connection' — never combinable with connection='all'/a "
    "list. Applies to every action, not just bulk_ingest."
)


def _coerce_properties_str_to_dict(v: Any) -> Any:
    """``mode="before"`` validator shared by ``_AddNodeAction``/
    ``_AddEdgeAction``: accept an already-JSON-encoded string for
    ``properties`` (the shape the base ``/graph/write`` route has
    historically taken there — see ``test_tiny_profile_serves_kg_over_
    gateway_with_zero_containers``) IN ADDITION to a plain JSON object,
    without widening the field's declared type away from the inherited
    ``dict[str, Any]`` (a wider ``dict | str`` annotation here would violate
    Liskov substitution against ``GraphWriteNodeRequest``/
    ``GraphWriteEdgeRequest``'s own ``properties: dict[str, Any]``, which
    mypy correctly rejects). An empty string normalizes to ``{}``; any other
    string is JSON-decoded (a non-dict/invalid JSON string is a clean 400 via
    the surrounding discriminated-union validation, not a silent pass).
    """
    if isinstance(v, str):
        return json.loads(v) if v.strip() else {}
    return v


class _AddNodeAction(GraphWriteNodeRequest):
    """``POST /graph/write``, ``action='add_node'`` — replaces
    ``POST /graph/write/node``.
    """

    action: Literal["add_node"] = Field(
        description="Fixed discriminator for this variant: 'add_node'."
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON object of node properties, OR an already-JSON-encoded "
            "string (both accepted; forwarded to the graph_write tool as a "
            "JSON-encoded string either way — the base /graph/write route "
            "has historically taken a raw pre-encoded string here, so both "
            "forms are supported for compatibility)."
        ),
        json_schema_extra={"examples": [{"label": "example"}]},
    )
    connection: str = Field(default="", description=_CONNECTION_FIELD_DESCRIPTION)
    graph: str = Field(default="", description=_GRAPH_FIELD_DESCRIPTION)

    _coerce_properties = field_validator("properties", mode="before")(
        _coerce_properties_str_to_dict
    )


class _AddEdgeAction(GraphWriteEdgeRequest):
    """``POST /graph/write``, ``action='add_edge'`` — replaces
    ``POST /graph/write/edge``.
    """

    action: Literal["add_edge"] = Field(
        description="Fixed discriminator for this variant: 'add_edge'."
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON object of edge properties, OR an already-JSON-encoded "
            "string (both accepted)."
        ),
    )
    connection: str = Field(default="", description=_CONNECTION_FIELD_DESCRIPTION)
    graph: str = Field(default="", description=_GRAPH_FIELD_DESCRIPTION)

    _coerce_properties = field_validator("properties", mode="before")(
        _coerce_properties_str_to_dict
    )


class _DeleteEdgeAction(GraphWriteEdgeDeleteRequest):
    """``action='delete_edge'`` — reachable via ``POST /graph/write`` (this
    variant) AND via ``DELETE /graph/write``
    (``graph_write_delete_edge_endpoint`` below, which validates the same
    ``GraphWriteEdgeDeleteRequest`` shape and hard-codes this action) —
    replaces ``DELETE /graph/write/edge``. Both are kept so neither an
    action-field-first caller nor a REST-verb-first caller loses the
    capability.
    """

    action: Literal["delete_edge"] = Field(
        description="Fixed discriminator for this variant: 'delete_edge'."
    )
    connection: str = Field(default="", description=_CONNECTION_FIELD_DESCRIPTION)
    graph: str = Field(default="", description=_GRAPH_FIELD_DESCRIPTION)


class _BulkIngestAction(GraphWriteBulkRequest):
    """``POST /graph/write``, ``action='bulk_ingest'`` — replaces
    ``POST /graph/write/bulk``.

    REGRESSION FIX: the deleted granular route forwarded ONLY ``nodes``,
    silently discarding ``idempotency_key``/``evidence``/``upsert``/
    ``connection``/``graph`` and always taking the non-idempotent
    ``BatchUpdate(upsert=True)`` path. This variant forwards all of them.
    """

    action: Literal["bulk_ingest"] = Field(
        description="Fixed discriminator for this variant: 'bulk_ingest'."
    )
    idempotency_key: str = Field(
        default="",
        description=(
            "Caller-owned idempotency key for this exact batch. Non-empty "
            "(or a non-empty 'evidence') routes the batch onto the engine's "
            "durably-idempotent ApplyChangeEnvelopes path, scoped by "
            "(tenant, graph, idempotency_key) — a replay reports "
            "'status':'skipped', never silently re-reported as fresh "
            "success. Empty uses the lighter BatchUpdate path, which has no "
            "per-call idempotency key."
        ),
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Evidence records ({'object_id','modality','locus',"
            "'content_digest'}) attached to the first node in 'nodes'. "
            "Non-empty routes the batch onto ApplyChangeEnvelopes instead "
            "of the lighter BatchUpdate path."
        ),
    )
    upsert: bool = Field(
        default=True,
        description=(
            "On the BatchUpdate (light) path only: True (default) MERGEs "
            "onto an existing id (idempotent); False INSERTs (a repeated "
            "edge becomes an additional parallel edge rather than "
            "replacing the prior one)."
        ),
    )
    connection: str = Field(default="", description=_CONNECTION_FIELD_DESCRIPTION)
    graph: str = Field(default="", description=_GRAPH_FIELD_DESCRIPTION)


class _LogChatAction(GraphWriteChatRequest):
    """``POST /graph/write``, ``action='log_chat'`` — replaces
    ``POST /graph/write/chat``.
    """

    action: Literal["log_chat"] = Field(
        description="Fixed discriminator for this variant: 'log_chat'."
    )
    connection: str = Field(default="", description=_CONNECTION_FIELD_DESCRIPTION)
    graph: str = Field(default="", description=_GRAPH_FIELD_DESCRIPTION)


class _RegisterExecutionAction(GraphWriteExecutionRequest):
    """``POST /graph/write``, ``action='register_execution'`` — replaces
    ``POST /graph/write/execution``.
    """

    action: Literal["register_execution"] = Field(
        description="Fixed discriminator for this variant: 'register_execution'."
    )
    connection: str = Field(default="", description=_CONNECTION_FIELD_DESCRIPTION)
    graph: str = Field(default="", description=_GRAPH_FIELD_DESCRIPTION)


class _OtherGraphWriteAction(GraphWriteRequest):
    """``POST /graph/write`` for every action never given its own granular
    route: ``delete_node``, ``register_external_graph``,
    ``compare_and_set``, ``store_memory``, ``recall_memory``,
    ``recall_media``, ``submit_sdd``, ``check_loop``. ``GraphWriteRequest``
    itself (imported, not redefined) already declares every field these
    actions read, with ``extra='allow'`` full passthrough — this subclass
    only narrows ``action`` to a Literal of those eight values so the
    discriminated union can tag-match it.
    """

    action: Literal[
        "delete_node",
        "register_external_graph",
        "compare_and_set",
        "store_memory",
        "recall_memory",
        "recall_media",
        "submit_sdd",
        "check_loop",
    ] = Field(
        description=(
            "One of: delete_node, register_external_graph, compare_and_set, "
            "store_memory, recall_memory, recall_media, submit_sdd, "
            "check_loop. See GraphWriteRequest's own field docs for which "
            "fields each of these reads."
        )
    )


GraphWriteAction = Annotated[
    _AddNodeAction
    | _AddEdgeAction
    | _DeleteEdgeAction
    | _BulkIngestAction
    | _LogChatAction
    | _RegisterExecutionAction
    | _OtherGraphWriteAction,
    Field(discriminator="action"),
]

_GRAPH_WRITE_ACTION_ADAPTER: TypeAdapter[Any] = TypeAdapter(GraphWriteAction)


async def _dispatch_graph_write_action(action_model: Any) -> Any:
    """Invoke ``_execute_tool("graph_write", ...)`` for one validated
    ``GraphWriteAction``. The six explicit branches mirror, field-for-field,
    what the now-deleted granular ``/graph/write/*`` routes used to forward
    (plus ``connection``/``graph``, and the ``bulk_ingest`` defect fix — see
    the class docstrings above); ``_OtherGraphWriteAction`` forwards its
    full body exactly as the pre-consolidation ``**body`` passthrough did.
    """
    if isinstance(action_model, _AddNodeAction):
        return await _execute_tool(
            "graph_write",
            action="add_node",
            node_id=action_model.node_id,
            node_type=action_model.node_type,
            properties=_to_json_str(action_model.properties),
            connection=action_model.connection,
            graph=action_model.graph,
        )
    if isinstance(action_model, _AddEdgeAction):
        return await _execute_tool(
            "graph_write",
            action="add_edge",
            source_id=action_model.source_id,
            target_id=action_model.target_id,
            rel_type=action_model.rel_type,
            properties=_to_json_str(action_model.properties),
            connection=action_model.connection,
            graph=action_model.graph,
        )
    if isinstance(action_model, _DeleteEdgeAction):
        return await _execute_tool(
            "graph_write",
            action="delete_edge",
            source_id=action_model.source_id,
            target_id=action_model.target_id,
            rel_type=action_model.rel_type,
            connection=action_model.connection,
            graph=action_model.graph,
        )
    if isinstance(action_model, _BulkIngestAction):
        return await _execute_tool(
            "graph_write",
            action="bulk_ingest",
            nodes=_to_json_str(action_model.nodes),
            idempotency_key=action_model.idempotency_key,
            evidence=_to_json_str(action_model.evidence),
            upsert=action_model.upsert,
            connection=action_model.connection,
            graph=action_model.graph,
        )
    if isinstance(action_model, _LogChatAction):
        return await _execute_tool(
            "graph_write",
            action="log_chat",
            agent_id=action_model.agent_id,
            properties=action_model.content,
            connection=action_model.connection,
            graph=action_model.graph,
        )
    if isinstance(action_model, _RegisterExecutionAction):
        return await _execute_tool(
            "graph_write",
            action="register_execution",
            agent_id=action_model.agent_id,
            connection=action_model.connection,
            graph=action_model.graph,
        )
    # _OtherGraphWriteAction: full passthrough parity with the
    # pre-consolidation **body forwarding.
    return await _execute_tool("graph_write", **action_model.model_dump())


async def graph_write_delete_node_endpoint(request: Request) -> JSONResponse:
    try:
        node_id = request.path_params.get("node_id", "")
        # Same DEFECT C field-name bug as `_AddNodeAction`'s dispatch above:
        # the tool parameter is ``node_id``, not ``id``.
        res = await _execute_tool("graph_write", action="delete_node", node_id=node_id)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except UnsupportedToolFieldError as e:
        return _external_error_response(e, status_code=400, code="invalid_request")
    except Exception as e:
        return _external_error_response(e)


async def graph_write_delete_edge_endpoint(request: Request) -> JSONResponse:
    """DELETE /graph/write — action='delete_edge' (replaces
    DELETE /graph/write/edge). Kept as a dedicated DELETE handler on the
    collapsed base path — see ``_DeleteEdgeAction``'s docstring above for
    why ``action='delete_edge'`` is ALSO reachable via POST.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        payload = GraphWriteEdgeDeleteRequest.model_validate(body or {})
    except ValidationError as e:
        return _external_error_response(e, status_code=400, code="invalid_request")
    try:
        res = await _execute_tool(
            "graph_write",
            action="delete_edge",
            source_id=payload.source_id,
            target_id=payload.target_id,
            rel_type=payload.rel_type,
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except UnsupportedToolFieldError as e:
        return _external_error_response(e, status_code=400, code="invalid_request")
    except Exception as e:
        return _external_error_response(e)


async def graph_write_external_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_write",
        lambda body: {
            "action": "register_external_graph",
            "endpoint_url": body.get("endpoint_url", ""),
            "graph_type": body.get("graph_type", ""),
        },
    )


async def graph_write_memory_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_write",
            action="store_memory",
            agent_id=body.get("agent_id", ""),
            node_type=body.get("memory_type", ""),
            properties=body.get("content", ""),
            nodes=_to_json_str(body.get("tags", [])),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_write_memory_recall_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_write",
        lambda body: {
            "action": "recall_memory",
            "properties": body.get("query", ""),
            "node_type": body.get("memory_type", ""),
        },
    )


async def graph_ontology_sync_packages_endpoint(request: Request) -> JSONResponse:
    """REST twin of ``graph_ontology action='sync_packages'`` (CONCEPT:AU-KG.ontology.federation-runtime).

    Federation: load every ontology ``.ttl`` contributed by installed fleet
    packages (``agent_utilities.ontology_providers``) through the shared ontology
    load path. Mirrors the generic ``POST /graph/ontology`` action twin as an
    explicit convenience route.
    """
    try:
        res = await _execute_tool("graph_ontology", action="sync_packages")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ontology_publish_stardog_endpoint(request: Request) -> JSONResponse:
    """REST twin of ``graph_ontology action='publish_stardog'`` (CONCEPT:AU-KG.ontology.stardog-catalog-overwrite).

    Push the platform's authoritative bundled TBox to a Stardog triplestore, overwriting
    the target named graph by default so an updated ontology updates the catalog.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_ontology",
            action="publish_stardog",
            named_graph=body.get("named_graph", ""),
            overwrite=bool(body.get("overwrite", True)),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ontology_import_stardog_endpoint(request: Request) -> JSONResponse:
    """REST twin of ``graph_ontology action='import_stardog'`` (CONCEPT:AU-KG.ontology.stardog-catalog-import).

    Consume the TBox already living in a Stardog database / named graph back into the
    engine, activating it for reasoning.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_ontology",
            action="import_stardog",
            named_graph=body.get("named_graph", ""),
            activate=bool(body.get("activate", True)),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_write_sdd_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_write",
        lambda body: {
            "action": "submit_sdd",
            "agent_id": body.get("agent_id", ""),
            "properties": body.get("content", ""),
        },
    )


# 4. Granular Graph Ingest endpoints
async def graph_ingest_submit_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_ingest",
            action="ingest",
            target_path=_to_json_str(body.get("target_path", "")),
            max_depth=int(body.get("max_depth", 3)),
            agent_id=body.get("agent_id", ""),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_corpus_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_ingest",
        lambda body: {
            "action": "corpus",
            "corpus_name": body.get("corpus_name", ""),
            "base_path": body.get("base_path", ""),
            "description": body.get("description", ""),
        },
    )


async def graph_ingest_jobs_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_ingest", action="jobs")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def connector_sources_endpoint(request: Request) -> JSONResponse:
    """List registered document-source connectors (CONCEPT:AU-ECO.connector.factory-ingestion-adaptor)."""
    try:
        res = await _execute_tool("source_connector", action="list")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def connector_run_endpoint(request: Request) -> JSONResponse:
    """Build + drain a document-source connector into the KG (CONCEPT:AU-ECO.connector.document-source-framework–4.29)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "source_connector",
            action="run",
            source_type=body.get("source_type", ""),
            config=body.get("config", {}) or {},
            connector_id=body.get("connector_id", ""),
            contextual=bool(body.get("contextual", True)),
            incremental=bool(body.get("incremental", True)),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_job_status_endpoint(request: Request) -> JSONResponse:
    try:
        job_id = request.path_params.get("job_id", "")
        res = await _execute_tool("graph_ingest", action="job_status", job_id=job_id)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_rebuild_indexes_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_ingest", action="rebuild_indexes")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_observe_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_ingest",
        lambda body: {
            "action": "observe",
            "target_path": body.get("target_path", ""),
            "agent_id": body.get("agent_id", ""),
        },
    )


async def graph_ingest_materialize_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_ingest", action="materialize")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_materialize_source_endpoint(request: Request) -> JSONResponse:
    """Persist an enterprise source extractor (camunda/aris/egeria) into the KG.

    Body: ``{"category": "camunda", "config": {...}}`` — ``category`` is the
    extractor key (required); ``config`` is an optional extractor-config dict.
    """
    try:
        body = await request.json()
        category = body.get("category") or body.get("corpus_name") or ""
        config = body.get("config")
        res = await _execute_tool(
            "graph_ingest",
            action="materialize_source",
            corpus_name=category,
            description=json.dumps(config) if isinstance(config, dict) else "",
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_sync_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_ingest", action="sync")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_reflect_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_ingest", action="reflect")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_agent_toolkit_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_ingest",
            action="agent_toolkit",
            target_path=_to_json_str(body.get("sources", [])),
            description=body.get("agent_card_path", ""),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_ingest_knowledge_pack_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_ingest",
        lambda body: {
            "action": "ingest_knowledge_pack",
            "target_path": body.get("target_path", ""),
        },
    )


# 5. Granular Graph Analyze endpoints
async def graph_analyze_synthesize_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request, "graph_research", lambda body: _query_top_k_kwargs(body, "synthesize")
    )


async def graph_analyze_process_writeback_endpoint(request: Request) -> JSONResponse:
    """Push KG process intelligence INTO Camunda instances / ARIS models.

    Body: ``{"target": "both|camunda|aris", "query": "id1,id2"}`` —
    ``target`` is the writeback scope (default ``both``); ``query`` is an
    optional comma-separated list of BusinessProcess node ids to limit to.
    """
    return await _run_json_endpoint(
        request,
        "graph_analyze",
        lambda body: {
            "action": "process_writeback",
            "target": body.get("target", "both"),
            "query": body.get("query", ""),
        },
    )


async def graph_analyze_deep_extract_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_research",
        lambda body: _query_top_k_kwargs(body, "deep_extract"),
    )


async def graph_analyze_background_research_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_research",
        lambda body: _query_top_k_kwargs(body, "background_research"),
    )


async def graph_analyze_relevance_sweep_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_research",
        lambda body: _query_top_k_kwargs(body, "relevance_sweep"),
    )


async def graph_analyze_blast_radius_endpoint(request: Request) -> JSONResponse:
    try:
        node_id = request.query_params.get("id", "")
        depth = int(request.query_params.get("depth", "2"))
        res = await _execute_tool(
            "graph_code", action="blast_radius", node_id=node_id, depth=depth
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_inspect_endpoint(request: Request) -> JSONResponse:
    try:
        target = request.query_params.get("target", "")
        res = await _execute_tool("graph_analyze", action="inspect", target=target)
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_call_graph_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=call_graph (CONCEPT:EG-KG.compute.type-scope-resolved-call): the
    type/scope-resolved call/inheritance graph for a symbol. ``id`` = symbol id;
    ``direction`` = callees | callers | inherits."""
    try:
        node_id = request.query_params.get("id", "")
        direction = request.query_params.get("direction") or request.query_params.get(
            "target", "callees"
        )
        res = await _execute_tool(
            "graph_code", action="call_graph", node_id=node_id, target=direction
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_similar_code_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=similar_code (CONCEPT:EG-KG.compute.model-free-similar-code): a
    symbol's model-free MinHash/LSH near-clone neighbours (embedder-free).
    ``id`` = symbol id; ``top_k`` optional."""
    try:
        node_id = request.query_params.get("id", "")
        top_k = int(request.query_params.get("top_k", "10"))
        res = await _execute_tool(
            "graph_code", action="similar_code", node_id=node_id, top_k=top_k
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_routes_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=routes (CONCEPT:AU-KG.compute.http-route-graph): the HTTP route
    graph — each Route, its handler, and the Service that serves it."""
    try:
        res = await _execute_tool("graph_code", action="routes")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_change_coupling_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=change_coupling (CONCEPT:AU-KG.ingest.mine-git-history-files): mine a
    repo's git history into FILE_CHANGES_WITH edges. Body: ``{repo, min_support?}``."""
    return await _run_json_endpoint(request, "graph_code", _change_coupling_kwargs)


async def graph_analyze_code_evolution_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=code_evolution (CONCEPT:AU-KG.enrichment.query-ingested-commit-history): query the
    ingested commit-history graph for codebase evolution. Body:
    ``{mode?, target?, top_k?}`` — mode = file|owners|hotspots|coupled,
    target = file path / subsystem path substring."""
    return await _run_json_endpoint(request, "graph_code", _code_evolution_kwargs)


async def graph_analyze_adr_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=adr (CONCEPT:AU-KG.compute.adr-crud): ADR CRUD. Body:
    ``{title?, status?, decision?}`` — title creates, empty lists."""
    return await _run_json_endpoint(
        request,
        "graph_code",
        lambda body: {
            "action": "adr",
            "query": body.get("title", ""),
            "target": body.get("status", ""),
            "node_id": body.get("decision", ""),
        },
    )


async def graph_analyze_harness_gate_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=harness_gate (CONCEPT:AU-AHE.evaluation.parity-surpass-scoreboard): validate a
    candidate harness-evolution state against the concentration/no-regression/pathology
    SHACL gate. Body: ``{edits:[…], variants?:[…], pathologies?:[…]}``."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        import json as _json

        res = await _execute_tool(
            "graph_evaluate", action="harness_gate", query=_json.dumps(body)
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_code_context_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_code action=code_context (CONCEPT:AU-KG.retrieval.synthesized-cited-answer): the
    synthesized, cited codebase Q&A. Body: ``{query, intent?(how|usage|impact),
    node_id?, top_k?, depth?, cross_repo?}``."""
    return await _run_json_endpoint(request, "graph_code", _code_context_kwargs)


async def graph_analyze_explain_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_explain action=explain (CONCEPT:AU-KG.retrieval.route-question-its-domain): the universal
    context plane. Body: ``{query, domain?, intent?, node_id?, top_k?, depth?}`` —
    routes to the domain provider (code | ops | …) and returns the cited answer."""
    return await _run_json_endpoint(request, "graph_explain", _explain_kwargs)


async def graph_analyze_cross_repo_usages_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=cross_repo_usages (CONCEPT:AU-KG.retrieval.every-usage-published-symbol): every
    usage of a published symbol across the fleet, grouped by repo. ``symbol`` /
    ``query`` = the symbol name; ``top_k`` optional."""
    try:
        symbol = request.query_params.get("symbol") or request.query_params.get(
            "query", ""
        )
        top_k = int(request.query_params.get("top_k", "200"))
        res = await _execute_tool(
            "graph_code", action="cross_repo_usages", query=symbol, top_k=top_k
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def _run_graph_code_scope_endpoint(request: Request, action: str) -> JSONResponse:
    try:
        scope = request.query_params.get("scope") or request.query_params.get(
            "target", ""
        )
        top_k = int(request.query_params.get("top_k", "10"))
        res = await _execute_tool(
            "graph_code", action=action, target=scope, top_k=top_k
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_code_metrics_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=code_metrics (CONCEPT:AU-KG.retrieval.god-nodes-communities): Graphify-
    style god nodes / communities / surprising connections over the :Code subgraph.
    ``scope`` (or ``target``) = optional file_path/source_system substring;
    ``top_k`` = section sizes."""
    return await _run_graph_code_scope_endpoint(request, "code_metrics")


async def graph_analyze_arch_report_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_analyze action=arch_report (CONCEPT:AU-KG.retrieval.architecture-report): the
    regenerable architecture report (GRAPH_REPORT.md analog) as Markdown + metrics.
    ``scope`` (or ``target``) = optional substring; ``top_k`` = section sizes."""
    return await _run_graph_code_scope_endpoint(request, "arch_report")


async def graph_analyze_context_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(request, "graph_explain", _context_kwargs)


async def graph_analyze_evaluate_alpha_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_evaluate", action="evaluate_alpha", target=body.get("target", "")
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_evaluate_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_evaluate", action="evaluate", target=body.get("target", "")
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_evolve_model_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_evaluate", action="evolve_model")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_forecast_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_evaluate", action="forecast")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_causal_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_evaluate", action="causal")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_invariant_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_evaluate", action="invariant")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_analyze_security_scan_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_analyze", action="security_scan", target=body.get("target", "")
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


# 7. Granular Graph Configure endpoints
async def graph_configure_secret_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_configure",
        lambda body: {
            "action": "set_secret",
            "config_key": body.get("config_key", ""),
            "config_value": body.get("config_value", ""),
        },
    )


async def graph_configure_vault_sync_endpoint(request: Request) -> JSONResponse:
    """REST twin of graph_configure action=vault_sync (CONCEPT:AU-OS.deployment.vault-first-routine-genesis)."""
    return await _run_json_endpoint(
        request,
        "graph_configure",
        lambda body: {
            "action": "vault_sync",
            "config_key": body.get("config_key", ""),
            "config_value": body.get("config_value", ""),
        },
    )


async def graph_configure_register_mcp_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        res = await _execute_tool(
            "graph_configure",
            action="register_mcp",
            config_key=body.get("config_key", ""),
            config_value=_to_json_str(body.get("config_value", {})),
        )
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


async def graph_configure_install_hooks_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_configure",
        lambda body: {
            "action": "install_hooks",
            "config_value": body.get("config_value", ""),
        },
    )


async def graph_configure_uninstall_hooks_endpoint(request: Request) -> JSONResponse:
    return await _run_json_endpoint(
        request,
        "graph_configure",
        lambda body: {
            "action": "uninstall_hooks",
            "config_value": body.get("config_value", ""),
        },
    )


async def graph_configure_doctor_endpoint(request: Request) -> JSONResponse:
    try:
        res = await _execute_tool("graph_configure", action="doctor")
        return JSONResponse({"status": "success", "result": safe_json_load(res)})
    except Exception as e:
        return _external_error_response(e)


# Default agent identity for provenance tracking
_AGENT_ID = setting("AGENT_ID", f"mcp-client-{uuid.uuid4().hex}")
_SESSION_ID = setting("SESSION_ID", uuid.uuid4().hex)


_ENGINE_LOCK = threading.Lock()


_ensure_process_authority_current = cast(Any, None)
_get_engine = cast(Any, None)
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
    from agent_utilities.mcp.server_factory import create_mcp_server

    is_readonly = False

    def _check_readonly():
        if is_readonly:
            return json.dumps(
                {
                    "error": "Knowledge Graph is currently in READ-ONLY mode due to database lock contention. "
                    "Write operations and ingestion are disabled until the other process releases the lock."
                }
            )
        return None

    # In embedded mode (bootstrap=False, e.g. the API gateway populating
    # REGISTERED_TOOLS) do NOT parse the host process's argv — pass an empty
    # command line so the factory uses defaults instead of choking on unrelated
    # flags (pytest/uvicorn args) with SystemExit.
    args, mcp, middlewares = create_mcp_server(
        name="graph-os",
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

    # Unauthenticated liveness + readiness for HTTP deployments (CONCEPT:AU-OS.deployment.liveness-vs-readiness-split).
    # Both dispatch into the ONE shared health-check core
    # (``observability.runtime_health.collect_health``) also used by the REST
    # gateway's ``/health``/``/health/ready`` and by ``graph_configure(action=
    # "health")`` — never a second implementation that can drift.
    #
    # ``/health`` is LIVENESS: it always answers 200 (this process itself is up
    # and answering requests) even when the body reports "unhealthy" — a
    # /health is a dependency-free, status-only liveness signal. The readiness
    # twin executes the truthful bounded collector on its reserved control lane,
    # but returns only ready/not_ready because both routes are intentionally
    # unauthenticated for kubelet. Detailed component data stays behind
    # graph_configure(action="health") and authenticated dashboard surfaces.
    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:  # noqa: ARG001
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

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
