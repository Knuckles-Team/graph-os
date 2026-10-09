"""Onboard the configured fleet MCP servers into epistemic-graph's catalog.

The fleet catalog joins live ``RegisterServer`` rows with current
``mcp_server`` components. A registration alone admits nothing. Onboarding
therefore does three things for every streamable-HTTP server in the MCP
config:

1. register the server, and renew any lease that lapses soon;
2. capture its served tools, prompts, skills and pack resources as a
   ConnectorPack through the connector SDK;
3. attest that catalog with EG and import the pack under EG's binding;
4. register its access contracts as unapproved virtual mappings.

GraphOS observes each child catalog over MCP, as the multiplexer does.
EG issues the binding through ``attest_self_served_catalog``; GraphOS must be
the bound importer of each fleet connector. One server's failure never stops
the others. A pass is idempotent, so the boot thread and the operator command
share it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Renew a registration whose lease lapses within this window.
RENEW_MARGIN_MS = 6 * 3600 * 1000
#: How often the boot thread re-runs a pass after the first.
PASS_INTERVAL_S = 1800.0
#: The per-request timeout of one child catalog capture.
CAPTURE_TIMEOUT_S = 60.0
_HTTP_TRANSPORTS = frozenset({"", "http", "streamable-http"})


@dataclass(frozen=True, slots=True)
class FleetEndpoint:
    """One configured fleet server: registry name and served MCP URL."""

    name: str
    url: str


@dataclass(slots=True)
class OnboardingReport:
    """What one onboarding pass did, per server name."""

    renewed: list[str] = field(default_factory=list)
    onboarded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def as_dict(self, *, private: bool = False) -> dict[str, Any]:
        """Render the report; ``private`` keeps only each failure's error type."""

        failed = {
            name: detail.partition(":")[0] if private else detail
            for name, detail in sorted(self.failed.items())
        }
        return {
            "renewed": sorted(self.renewed),
            "onboarded": sorted(self.onboarded),
            "failed": failed,
        }


def _endpoint(name: object, spec: object) -> FleetEndpoint | None:
    if not isinstance(name, str) or not isinstance(spec, dict):
        return None
    url = spec.get("url")
    transport = str(spec.get("transport") or spec.get("type") or "")
    if spec.get("disabled") or not isinstance(url, str):
        return None
    if not url.startswith(("http://", "https://")):
        return None
    if transport.replace("_", "-") not in _HTTP_TRANSPORTS:
        return None
    return FleetEndpoint(name=name, url=url)


def fleet_endpoints(
    raw: bytes, *, exclude: Collection[str] = ()
) -> tuple[FleetEndpoint, ...]:
    """Return every enabled streamable-HTTP server in an MCP config payload."""

    from graph_os.gateway.config import _parse_mcp_servers

    endpoints = (
        _endpoint(name, spec) for name, spec in _parse_mcp_servers(raw).items()
    )
    return tuple(
        sorted(
            (item for item in endpoints if item and item.name not in exclude),
            key=lambda item: item.name,
        )
    )


def configured_fleet_endpoints(
    *, exclude: Collection[str] = (), path: Path | None = None
) -> tuple[FleetEndpoint, ...]:
    """Read the deployment's MCP config; an absent file has no fleet."""

    from agent_utilities.core.paths import mcp_config_path

    config = path or mcp_config_path()
    if not config.is_file():
        return ()
    return fleet_endpoints(config.read_bytes(), exclude=exclude)


def self_served_connectors() -> tuple[str, ...]:
    """The connectors GraphOS serves in process; never fleet children."""

    from graph_os.semantic_content import required_content_connectors

    return required_content_connectors()


def _resource_scheme(resource: Any) -> str:
    scheme, separator, _ = str(getattr(resource, "uri", "")).partition("://")
    return scheme if separator else ""


class _PackResourceSession:
    """A session view that lists only ConnectorPack-schemed resources.

    A fleet server may also serve live data resources. Those are not catalog
    content, so the capture never reads them.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def list_resources(self) -> list[Any]:
        from agent_connector_sdk.artifacts.resources import _RESOURCE_SCHEMES

        return [
            resource
            for resource in await self._session.list_resources()
            if _resource_scheme(resource) in _RESOURCE_SCHEMES
        ]

    async def read_resource(self, uri: str) -> str:
        return await self._session.read_resource(uri)


def _capture_kinds() -> tuple[Any, ...]:
    from agent_connector_sdk.artifacts.prompts import PromptArtifactKind
    from agent_connector_sdk.artifacts.resources import ResourceArtifactKind
    from agent_connector_sdk.artifacts.skills import SkillArtifactKind
    from agent_connector_sdk.artifacts.tools import ToolArtifactKind

    class PackResourceKind(ResourceArtifactKind):
        async def list_entries(self, session: Any, server: Any) -> Any:
            return await super().list_entries(_PackResourceSession(session), server)

    return (
        ToolArtifactKind(),
        SkillArtifactKind(),
        PromptArtifactKind(),
        PackResourceKind(),
    )


def _child_auth() -> Any:
    """GraphOS's service credential for a child, as the multiplexer sends it."""

    from agent_utilities.mcp.client_credentials import child_auth
    from agent_utilities.mcp.httpx_boundary import coerce_httpx2_auth

    return coerce_httpx2_auth(child_auth(None))


async def capture_server_pack(endpoint: FleetEndpoint, *, auth: Any = None) -> Any:
    """Capture one live fleet server's catalog as a ConnectorPack."""

    from agent_connector_sdk.artifacts.pack import build_content_pack
    from agent_connector_sdk.ports.session import TransportEndpoint
    from agent_connector_sdk.transports.mcp import McpTransport

    transport_endpoint = TransportEndpoint(
        url=endpoint.url, timeout_seconds=CAPTURE_TIMEOUT_S, auth=auth
    )
    async with McpTransport().session(transport_endpoint) as session:
        return await build_content_pack(
            session, connector=endpoint.name, kinds=_capture_kinds()
        )


@dataclass(frozen=True, slots=True)
class FleetOnboarding:
    """The EG authority one pass runs under."""

    engine: Any
    session: Any
    #: Replaces :func:`capture_server_pack`, for example in tests.
    capture: Any = None
    #: The virtual catalog that access contracts register into.
    virtual_catalog: Any = None

    @property
    def tenant_client(self) -> Any:
        return self.engine.graph_compute.for_graph(str(self.session.graph)).async_client

    @property
    def commons_client(self) -> Any:
        from graph_os.deployment.semantic_provisioning import COMMONS_GRAPH

        return self.engine.graph_compute.for_graph(COMMONS_GRAPH).async_client

    async def renew(
        self, endpoints: Mapping[str, str], *, now_ms: int | None = None
    ) -> tuple[str, ...]:
        """Register every endpoint whose lease is absent or lapses soon."""

        from graph_os.deployment.semantic_provisioning import (
            COMMONS_GRAPH,
            _bind_session_graph,
            ensure_server_registrations,
        )

        with _bind_session_graph(COMMONS_GRAPH):
            return await ensure_server_registrations(
                self.commons_client,
                endpoints,
                renew_margin_ms=RENEW_MARGIN_MS,
                now_ms=now_ms,
            )

    async def onboard(self, endpoint: FleetEndpoint, *, auth: Any) -> bool:
        """Capture one server's catalog; attest and import only when it changed.

        Returns whether EG's catalog actually changed. A server already
        admitted with an unchanged pack is never re-attested: re-attesting
        unchanged content reuses attest's digest-derived idempotency key
        under a freshly randomized request (``request_id``, ``attempt_nonce``,
        ``created_at_ms``), which EG refuses as ``IDEMPOTENCY_CONFLICT``
        (GRAPHOS-FLEET-R027). The caller treats an unchanged server as
        renewed rather than re-onboarded.
        """

        from graph_os.deployment.semantic_provisioning import import_attested_pack

        capture = self.capture or capture_server_pack
        pack = await capture(endpoint, auth=auth)
        changed = not await self.pack_unchanged(endpoint, pack)
        if changed:
            await import_attested_pack(
                self.engine,
                self.session,
                client=self.tenant_client,
                commons=self.commons_client,
                pack=pack,
            )
        self.register_contracts(endpoint, pack)
        return changed

    async def pack_unchanged(self, endpoint: FleetEndpoint, pack: Any) -> bool:
        """True when EG already holds ``pack`` unchanged under its current head.

        Reads EG's durable ``ConnectorPack.status`` for the connector -- a
        status read, never a mutation -- and recomputes the pack digest with
        the catalog binding the head itself carries. A mismatch (new content)
        or an absent head (never onboarded) both mean "not unchanged", so the
        caller proceeds to a real attest and import.
        """

        from epistemic_graph.connector_pack import pack_digest
        from epistemic_graph.generated.connector_pack import ConnectorPackStatusRequest
        from epistemic_graph.generated.storage import send_connector_pack_status

        from graph_os.deployment.semantic_provisioning import _bind_session_graph

        tenant_id = str(self.session.engine_verified_context()["tenant"])
        graph = str(self.session.graph)
        with _bind_session_graph(graph):
            status = await send_connector_pack_status(
                self.tenant_client,
                ConnectorPackStatusRequest(
                    connector=endpoint.name, tenant_id=tenant_id
                ),
                graph,
            )
        head = status.head
        if head is None:
            return False
        digest = pack_digest(
            endpoint.name, head.catalog, pack.archive.server, pack.archive.entries
        )
        return head.pack_digest == digest

    def register_contracts(self, endpoint: FleetEndpoint, pack: Any) -> None:
        """Register the pack's access contracts as unapproved mappings."""

        from graph_os.fleet.access_contracts import register_access_contracts

        added = register_access_contracts(
            pack, connector=endpoint.name, catalog=self.virtual_catalog
        )
        if added:
            logger.info("%s: %d unapproved access mappings", endpoint.name, added)

    async def self_endpoints(
        self, connectors: Sequence[str], served_url: str
    ) -> dict[str, str]:
        """Self-served registrations to renew, at their configured or live URL."""

        from graph_os.deployment.semantic_provisioning import (
            COMMONS_GRAPH,
            _bind_session_graph,
            _registry_page,
        )

        with _bind_session_graph(COMMONS_GRAPH):
            page = await _registry_page(self.commons_client)
        live = {entry.name: entry.url for entry in page.entries}
        urls = {name: served_url or live.get(name, "") for name in connectors}
        return {name: url for name, url in urls.items() if url}


def _failure(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:512]


async def _renew_all(
    onboarding: FleetOnboarding,
    endpoints: Sequence[FleetEndpoint],
    *,
    served_url: str,
    report: OnboardingReport,
) -> None:
    targets = await onboarding.self_endpoints(self_served_connectors(), served_url)
    targets.update({item.name: item.url for item in endpoints})
    report.renewed.extend(await onboarding.renew(targets))


async def _onboard_all(
    onboarding: FleetOnboarding,
    pending: Sequence[FleetEndpoint],
    report: OnboardingReport,
) -> None:
    if not pending:
        return
    try:
        auth = _child_auth()
    except Exception as exc:  # no service identity: nothing can be captured
        report.failed["child-auth"] = _failure(exc)
        logger.warning("fleet onboarding has no child credential: %s", exc)
        return
    for endpoint in pending:
        try:
            changed = await onboarding.onboard(endpoint, auth=auth)
        except Exception as exc:  # one child must not stop the fleet
            report.failed[endpoint.name] = _failure(exc)
            logger.warning("fleet onboarding of %s failed: %s", endpoint.name, exc)
        else:
            (report.onboarded if changed else report.renewed).append(endpoint.name)


async def onboard_fleet(
    onboarding: FleetOnboarding,
    endpoints: Sequence[FleetEndpoint],
    *,
    served_url: str = "",
    admitted: Collection[str] = (),
) -> OnboardingReport:
    """Run one idempotent pass: renew every lease, onboard every new server.

    ``admitted`` names servers the serving catalog already holds; a pass
    skips their capture and import but still renews their leases.
    """

    report = OnboardingReport()
    try:
        await _renew_all(onboarding, endpoints, served_url=served_url, report=report)
    except Exception as exc:  # the registry may be briefly unavailable
        report.failed["registry"] = _failure(exc)
        logger.warning("fleet registration renewal failed: %s", exc)
        return report
    pending = [item for item in endpoints if item.name not in admitted]
    await _onboard_all(onboarding, pending, report)
    return report


async def refresh_serving_catalog(
    multiplexer: Any, report: OnboardingReport, fleet: Collection[str]
) -> bool:
    """Re-read the EG catalog when a pass changed what it can admit.

    The multiplexer refuses a refresh once a child runs. Then the new
    servers wait for the next restart, and the refusal is logged. The
    refresh runs on the onboarding thread; it swaps the catalog only while
    no child session exists.
    """

    current = set(multiplexer.load_catalog())
    changed = (set(report.onboarded) | set(report.renewed)) & set(fleet)
    if not changed - current:
        return False
    try:
        await multiplexer.refresh_engine_catalog()
    except Exception as exc:  # serving continues on the prior catalog
        logger.warning("fleet catalog refresh after onboarding refused: %s", exc)
        return False
    return True


@dataclass(slots=True)
class FleetOnboardingService:
    """The boot-time background pass and its periodic renewal."""

    onboarding: FleetOnboarding
    multiplexer: Any
    served_url: str = ""
    interval_s: float = PASS_INTERVAL_S
    stop: threading.Event = field(default_factory=threading.Event)

    def endpoints(self) -> tuple[FleetEndpoint, ...]:
        exclude = {*self_served_connectors(), *self._skipped()}
        return configured_fleet_endpoints(exclude=exclude)

    def _skipped(self) -> Collection[str]:
        return getattr(self.multiplexer, "_skip_servers", None) or ()

    async def run_pass(self) -> OnboardingReport:
        endpoints = self.endpoints()
        report = await onboard_fleet(
            self.onboarding,
            endpoints,
            served_url=self.served_url,
            admitted=set(self.multiplexer.load_catalog()),
        )
        fleet = [item.name for item in endpoints]
        await refresh_serving_catalog(self.multiplexer, report, fleet)
        self.multiplexer._fleet_onboarding = report.as_dict()
        logger.info("fleet onboarding pass: %s", report.as_dict())
        return report

    def passes(self) -> Iterator[None]:
        """Yield before each pass: one now, then one per interval until stopped."""

        yield
        while not self.stop.wait(self.interval_s):
            yield

    def run_forever(self) -> None:
        from agent_utilities.api.session import use_session
        from agent_utilities.security.brain_context import use_actor

        session = self.onboarding.session
        with use_actor(session.actor), use_session(session):
            for _ in self.passes():
                self._run_pass_safely()

    def _run_pass_safely(self) -> None:
        try:
            asyncio.run(self.run_pass())
        except Exception as exc:  # serving must outlive any onboarding fault
            logger.warning("fleet onboarding pass failed: %s", exc)

    def start(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.run_forever, name="fleet-onboarding", daemon=True
        )
        thread.start()
        return thread


def configured_served_url() -> str:
    """The MCP URL GraphOS serves at, from ``GRAPH_OS_SERVED_MCP_URL``."""

    from agent_utilities.core.config import setting

    return str(setting("GRAPH_OS_SERVED_MCP_URL", "") or "")


def start_fleet_onboarding(
    *, engine: Any, session: Any, multiplexer: Any, served_url: str | None = None
) -> threading.Thread | None:
    """Start the background onboarding service; never raise into boot.

    ``served_url`` defaults to :func:`configured_served_url`. Without it, the
    self-served registrations renew at their live URL.
    """

    if getattr(engine, "graph_compute", None) is None or multiplexer is None:
        logger.warning("fleet onboarding disabled: no engine or multiplexer")
        return None
    service = FleetOnboardingService(
        onboarding=FleetOnboarding(engine=engine, session=session),
        multiplexer=multiplexer,
        served_url=configured_served_url() if served_url is None else served_url,
    )
    return service.start()


__all__ = [
    "FleetEndpoint",
    "FleetOnboarding",
    "FleetOnboardingService",
    "OnboardingReport",
    "capture_server_pack",
    "configured_fleet_endpoints",
    "fleet_endpoints",
    "onboard_fleet",
    "refresh_serving_catalog",
    "start_fleet_onboarding",
]
