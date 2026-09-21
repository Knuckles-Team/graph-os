"""Graph OS authority for attended remote calls to browser-local WebMCP tools."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

from agent_utilities.observability.langfuse_exporter import get_langfuse_exporter
from agent_utilities.orchestration.action_policy import get_action_policy

from graph_os.browser_control.browser_control_api import (
    BrowserCallReceipt,
    BrowserChannelBinding,
)
from graph_os.browser_control.browser_control_attendance import BrowserAttendanceMixin
from graph_os.browser_control.browser_control_binding import control_authority
from graph_os.browser_control.browser_control_cancellation import (
    BrowserCancellationMixin,
)
from graph_os.browser_control.browser_control_channel import BrowserChannelMixin
from graph_os.browser_control.browser_control_dispatch import BrowserDispatchMixin
from graph_os.browser_control.browser_control_lease import BrowserLeaseMixin
from graph_os.browser_control.browser_control_outcome import BrowserOutcomeMixin
from graph_os.browser_control.browser_control_policy import BrowserPolicyMixin
from graph_os.browser_control.browser_control_provenance import BrowserProvenanceMixin
from graph_os.browser_control.browser_control_state import _ActiveCall, _ChannelState

SyncRunner = Callable[[Callable[[], Any]], Awaitable[Any]]
SessionRevalidator = Callable[[BrowserChannelBinding], Awaitable[bool]]


class BrowserControlService(
    BrowserAttendanceMixin,
    BrowserChannelMixin,
    BrowserLeaseMixin,
    BrowserPolicyMixin,
    BrowserDispatchMixin,
    BrowserCancellationMixin,
    BrowserProvenanceMixin,
    BrowserOutcomeMixin,
):
    """The one Graph OS browser-control implementation shared by all projections."""

    authority: Literal["graph-os"] = "graph-os"
    supports_durable_fences: Literal[True] = True
    supports_durable_audit: Literal[True] = True
    supports_attended_leases: Literal[True] = True
    supports_live_revalidation: Literal[True] = True
    supports_backchannel_revalidation: Literal[True] = True
    supports_catalog_verification: Literal[True] = True

    def __init__(
        self,
        engine: Any,
        *,
        sync_runner: SyncRunner,
        session_revalidator: SessionRevalidator,
        action_policy: Any | None = None,
        langfuse_exporter: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(sync_runner):
            raise TypeError("sync_runner must be callable")
        if not callable(session_revalidator):
            raise TypeError("session_revalidator must be callable")
        if control_authority(engine) is None:
            raise ValueError("engine lacks native browser-control authorities")
        self._engine = engine
        self._authority = cast(Any, control_authority(engine))
        self._sync_runner = sync_runner
        self._session_revalidator = session_revalidator
        self._policy = action_policy or get_action_policy(engine)
        self._langfuse = (
            langfuse_exporter
            if langfuse_exporter is not None
            else get_langfuse_exporter()
        )
        self._clock = clock
        self._channels: dict[str, _ChannelState] = {}
        self._active_calls: dict[str, _ActiveCall] = {}
        self._completed: OrderedDict[str, BrowserCallReceipt] = OrderedDict()
        self._completed_authority: dict[str, tuple[_ChannelState, str]] = {}
        self._lock = asyncio.Lock()

    async def _validate_binding_authority(self, binding: BrowserChannelBinding) -> None:
        """Revalidate local session, expiries, and the configured IdP backchannel."""

        self._validate_ambient_binding(binding)
        now = self._clock()
        if not (
            now < binding.attended_arm_expires_at <= binding.access_token_expires_at
        ):
            raise PermissionError("browser attended or access-token authority expired")
        result = self._session_revalidator(binding)
        if not inspect.isawaitable(result):
            raise TypeError("session_revalidator must return an awaitable")
        if await result is not True:
            raise PermissionError("browser login authority is no longer live")

    @staticmethod
    def _validate_ambient_binding(binding: BrowserChannelBinding) -> None:
        """Reject a foreign caller without disturbing the legitimate channel."""

        from agent_utilities.knowledge_graph.core.session import resolve_session

        resolve_session(binding.session, required_scope="kg:write")
        if str(getattr(binding.session.actor, "actor_type", "")) != "human":
            raise PermissionError("browser control requires a verified human actor")


def browser_control_factory_kwargs(
    app_factory: Callable[..., Any],
    engine: Any,
    sync_runner: SyncRunner,
    session_revalidator: SessionRevalidator | None = None,
) -> dict[str, Any]:
    """Inject the GraphOS port only into a contract-bearing WebUI factory.

    The composition root supplies the already-admitted engine.  Browser control
    never discovers a second AU engine singleton or creates an alternate durable
    authority.
    """

    try:
        supports_port = "browser_control" in inspect.signature(app_factory).parameters
    except (TypeError, ValueError):
        return {}
    if not supports_port:
        return {}
    if not callable(session_revalidator):
        try:
            from agent_webui.browser_control import (
                revalidate_browser_control_session,
            )
        except ImportError:
            return {"browser_control": None}
        session_revalidator = revalidate_browser_control_session
    try:
        service = BrowserControlService(
            engine,
            sync_runner=sync_runner,
            session_revalidator=session_revalidator,
        )
    except (ImportError, RuntimeError, TypeError, ValueError):
        service = None
    return {"browser_control": service}


__all__ = [
    "BrowserControlService",
    "SessionRevalidator",
    "browser_control_factory_kwargs",
]
