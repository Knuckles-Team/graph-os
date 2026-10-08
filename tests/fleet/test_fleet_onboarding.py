"""Fleet onboarding: register, attest and import each configured child catalog.

The EG transport is faked at ``client._send`` so the generated senders, models
and decoders run for real. Each child catalog is captured from an in-process
FastMCP server through the real SDK pack builder.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.knowledge_graph.core.session import GraphSession
from agent_utilities.orchestration.action_policy import (
    ActionDecision,
    ActionRequest,
    PolicyDisposition,
    PolicyReceipt,
)
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext

from graph_os.deployment import production_ops
from graph_os.deployment.semantic_provisioning import ensure_server_registrations
from graph_os.fleet import onboarding
from graph_os.fleet.onboarding import (
    RENEW_MARGIN_MS,
    FleetEndpoint,
    FleetOnboarding,
    FleetOnboardingService,
    OnboardingReport,
    fleet_endpoints,
    onboard_fleet,
    refresh_serving_catalog,
    start_fleet_onboarding,
)

TENANT = "tenant-a"
GRAPH = "tenant__tenant-a__default"
NOW_MS = 1_800_000_000_000
OWNER = "principal:sha256:" + "e" * 64
SELF_URL = "https://graph-os.example/mcp"
ALPHA = FleetEndpoint("alpha-mcp", "http://alpha-mcp.example/mcp")
BETA = FleetEndpoint("beta-mcp", "http://beta-mcp.example/mcp")


class _Registry:
    """A fake EG engine answering the registry and catalog-authority ops."""

    def __init__(self, leases: dict[str, int] | None = None) -> None:
        self.leases = dict(leases or {})
        self.registers: list[tuple[dict[str, Any], str]] = []
        self.attested: list[str] = []
        self.list_error: Exception | None = None
        self.graph_compute = self

    def for_graph(self, _graph: str) -> Any:
        return SimpleNamespace(async_client=self)

    async def _send(
        self, method: str, params: Any, graph: Any, *, idempotency_key: Any = None
    ) -> Any:
        op = (params or {}).get("op") if method == "ConnectorPack" else None
        name = op["op"] if isinstance(op, dict) else method
        return getattr(self, f"_on_{name}")(params, idempotency_key)

    def _on_ListRegisteredServers(self, _params: Any, _key: Any) -> Any:
        if self.list_error is not None:
            raise self.list_error
        entries = [
            {
                "name": name,
                "url": SELF_URL if name == "graph-os" else f"http://{name}/mcp",
                "transport": "streamable_http",
                "desired": "enabled",
                "resources": {},
                "ttl_secs": 86_400,
                "registered_at_ms": 1,
                "last_heartbeat_ms": 1,
                "lease_expires_at_ms": lease,
            }
            for name, lease in sorted(self.leases.items())
        ]
        return {
            "schema_version": 1,
            "entries": entries,
            "next_cursor": None,
            "observed_at_ms": 1,
            "total_live": len(entries),
            "registry_revision": 3,
            "registry_digest": "3" * 64,
        }

    def _on_RegisterServer(self, params: Any, key: Any) -> Any:
        self.registers.append((params, key))
        self.leases[params["name"]] = NOW_MS + params["ttl_secs"] * 1000
        return "registered"

    def _on_catalog_authority_status(self, _params: Any, _key: Any) -> Any:
        return None

    def _on_attest_self_served_catalog(self, params: Any, _key: Any) -> Any:
        self.attested.append(params["op"]["request"]["server_name"])
        return {
            "configuration_revision": 1,
            "catalog_generation": 1,
            "snapshot_digest": "5" * 64,
            "child_connection_generation": 1,
            "authorization_scope_digest": "4" * 64,
        }

    def _on_catalog_request_owner_principal(self, _params: Any, _key: Any) -> Any:
        return OWNER


class _RecordingSink:
    """Stands in for the SDK sink; resolves the real AU import authority."""

    imported: list[tuple[str, Any]] = []

    def __init__(self, _client: Any, authority: Any) -> None:
        self._authority = authority

    async def import_pack(self, pack: Any) -> Any:
        binding, _context = await self._authority(pack.connector)
        _RecordingSink.imported.append((pack.connector, binding))
        return SimpleNamespace(result="imported")


class _AllowPolicy:
    def decide(self, request: ActionRequest) -> ActionDecision:
        return ActionDecision(
            decision="allow",
            tier="auto_notify",
            request=request,
            receipt=PolicyReceipt(
                receipt_id="action_decision:onboarding",
                request_digest=request.digest(),
                disposition=PolicyDisposition.APPROVE,
                policy_origin="test",
            ),
        )


def _session() -> GraphSession:
    return GraphSession(
        actor=ActorContext(
            actor_id="service:graph-os",
            actor_type=ActorType.AUTOMATED_SERVICE,
            tenant_id=TENANT,
            authenticated=True,
        ),
        tenant=TENANT,
        scopes=frozenset({"agent:pack-control", "connector:catalog-attest"}),
        graph=GRAPH,
        audience="graph-os",
        policy_version="policy-a",
    )


async def _served_pack(endpoint: FleetEndpoint, *, auth: Any = None) -> Any:
    """Capture a child catalog from an in-process server via the real SDK."""

    from agent_connector_sdk.artifacts.pack import build_content_pack
    from agent_connector_sdk.ports.session import TransportEndpoint
    from agent_connector_sdk.transports.mcp import McpTransport
    from fastmcp import FastMCP

    server: FastMCP[object] = FastMCP(endpoint.name, version="1.0.0")

    @server.tool
    def lookup(query: str) -> str:
        """Look one record up."""
        return query

    async with McpTransport().session(TransportEndpoint(in_process=server)) as session:
        return await build_content_pack(
            session, connector=endpoint.name, kinds=onboarding._capture_kinds()
        )


@pytest.fixture
def eg(monkeypatch: pytest.MonkeyPatch) -> _Registry:
    registry = _Registry()
    monkeypatch.setattr(
        "agent_utilities.api.provisioning.get_action_policy",
        lambda _engine: _AllowPolicy(),
    )
    monkeypatch.setattr(
        "agent_connector_sdk.sinks.epistemic_graph.EpistemicGraphSink",
        _RecordingSink,
    )
    monkeypatch.setattr(onboarding, "_child_auth", lambda: None)
    monkeypatch.setattr(
        onboarding, "self_served_connectors", lambda: ("graph-os", "agent-utilities")
    )
    _RecordingSink.imported = []
    return registry


def _onboarding(registry: _Registry, capture: Any = _served_pack) -> FleetOnboarding:
    return FleetOnboarding(engine=registry, session=_session(), capture=capture)


def _run_in_session(coro: Any) -> Any:
    from agent_utilities.api.session import use_session

    session = _session()
    with use_session(session):
        return asyncio.run(coro)


def test_endpoints_keep_only_enabled_streamable_http_servers() -> None:
    raw = json.dumps(
        {
            "mcpServers": {
                "zeta-mcp": {"url": "http://zeta/mcp", "transport": "streamable-http"},
                "alpha-mcp": {"url": "https://alpha/mcp"},
                "off-mcp": {"url": "http://off/mcp", "disabled": True},
                "stdio-mcp": {"command": "run-stdio"},
                "sse-mcp": {"url": "http://sse/sse", "transport": "sse"},
                "file-mcp": {"url": "file:///tmp/mcp"},
                "graph-os": {"url": SELF_URL},
            }
        }
    ).encode()

    endpoints = fleet_endpoints(raw, exclude={"graph-os"})

    assert endpoints == (
        FleetEndpoint("alpha-mcp", "https://alpha/mcp"),
        FleetEndpoint("zeta-mcp", "http://zeta/mcp"),
    )


def test_renewal_reregisters_only_lapsing_or_absent_leases() -> None:
    registry = _Registry(
        leases={
            "fresh-mcp": NOW_MS + RENEW_MARGIN_MS + 1,
            "lapsing-mcp": NOW_MS + RENEW_MARGIN_MS - 1,
        }
    )
    endpoints = {
        "fresh-mcp": "http://fresh/mcp",
        "lapsing-mcp": "http://lapsing/mcp",
        "absent-mcp": "http://absent/mcp",
    }

    renewed = asyncio.run(
        ensure_server_registrations(
            registry, endpoints, renew_margin_ms=RENEW_MARGIN_MS, now_ms=NOW_MS
        )
    )

    assert renewed == ("lapsing-mcp", "absent-mcp")
    window = NOW_MS // RENEW_MARGIN_MS
    assert [key for _params, key in registry.registers] == [
        f"graph-os:register-server:lapsing-mcp:http://lapsing/mcp:{window}",
        f"graph-os:register-server:absent-mcp:http://absent/mcp:{window}",
    ]
    assert all(params["ttl_secs"] == 86_400 for params, _key in registry.registers)


def test_fresh_store_pass_registers_attests_and_imports_each_server(
    eg: _Registry,
) -> None:
    report = _run_in_session(
        onboard_fleet(_onboarding(eg), (ALPHA, BETA), served_url=SELF_URL)
    )

    assert report.failed == {}
    assert sorted(report.onboarded) == ["alpha-mcp", "beta-mcp"]
    assert sorted(report.renewed) == [
        "agent-utilities",
        "alpha-mcp",
        "beta-mcp",
        "graph-os",
    ]
    registered = {params["name"]: params["url"] for params, _key in eg.registers}
    assert registered["alpha-mcp"] == ALPHA.url
    assert registered["graph-os"] == SELF_URL
    assert eg.attested == ["alpha-mcp", "beta-mcp"]
    assert [connector for connector, _binding in _RecordingSink.imported] == [
        "alpha-mcp",
        "beta-mcp",
    ]
    assert all(
        binding.catalog_generation == 1 for _c, binding in _RecordingSink.imported
    )


def test_one_failing_server_never_stops_the_fleet(eg: _Registry) -> None:
    async def capture(endpoint: FleetEndpoint, *, auth: Any = None) -> Any:
        if endpoint is ALPHA:
            raise ConnectionError("alpha refused the session")
        return await _served_pack(endpoint, auth=auth)

    report = _run_in_session(onboard_fleet(_onboarding(eg, capture), (ALPHA, BETA)))

    assert report.onboarded == ["beta-mcp"]
    assert report.failed == {"alpha-mcp": "ConnectionError: alpha refused the session"}
    assert report.as_dict(private=True)["failed"] == {"alpha-mcp": "ConnectionError"}


def test_admitted_servers_renew_without_recapture(eg: _Registry) -> None:
    captured: list[str] = []

    async def capture(endpoint: FleetEndpoint, *, auth: Any = None) -> Any:
        captured.append(endpoint.name)
        return await _served_pack(endpoint, auth=auth)

    report = _run_in_session(
        onboard_fleet(_onboarding(eg, capture), (ALPHA, BETA), admitted={"alpha-mcp"})
    )

    assert captured == ["beta-mcp"]
    assert "alpha-mcp" in report.renewed


def test_self_registrations_renew_at_their_live_url_without_a_served_url(
    eg: _Registry,
) -> None:
    eg.leases["graph-os"] = 1

    report = _run_in_session(onboard_fleet(_onboarding(eg), ()))

    assert report.renewed == ["graph-os"]
    assert eg.registers[0][0]["url"] == SELF_URL


def test_registry_outage_reports_and_captures_nothing(eg: _Registry) -> None:
    eg.list_error = ConnectionError("registry unavailable")

    report = _run_in_session(onboard_fleet(_onboarding(eg), (ALPHA,)))

    assert report.onboarded == [] and report.renewed == []
    assert report.failed == {"registry": "ConnectionError: registry unavailable"}


def test_missing_child_credential_reports_once(
    eg: _Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_identity() -> Any:
        raise RuntimeError("Outbound MCP service identity is incomplete")

    monkeypatch.setattr(onboarding, "_child_auth", no_identity)

    report = _run_in_session(onboard_fleet(_onboarding(eg), (ALPHA, BETA)))

    assert report.onboarded == []
    assert list(report.failed) == ["child-auth"]


class _Mux:
    def __init__(self, catalog: dict[str, Any], error: Exception | None = None):
        self.catalog = catalog
        self.error = error
        self.refreshes = 0
        self._skip_servers = {"mcp-multiplexer", "graph-os"}
        self._fleet_onboarding: dict[str, Any] | None = None

    def load_catalog(self) -> dict[str, Any]:
        return self.catalog

    async def refresh_engine_catalog(self) -> dict[str, Any]:
        self.refreshes += 1
        if self.error is not None:
            raise self.error
        return self.catalog


def test_refresh_runs_only_when_a_new_fleet_server_is_admissible() -> None:
    report = OnboardingReport(renewed=["graph-os", "alpha-mcp"], onboarded=[])
    mux = _Mux({"alpha-mcp": {}})

    assert asyncio.run(refresh_serving_catalog(mux, report, ["alpha-mcp"])) is False
    report.onboarded.append("beta-mcp")
    assert (
        asyncio.run(refresh_serving_catalog(mux, report, ["alpha-mcp", "beta-mcp"]))
        is True
    )
    assert mux.refreshes == 1


def test_refused_refresh_keeps_serving_the_prior_catalog() -> None:
    mux = _Mux({}, error=RuntimeError("children already started"))
    report = OnboardingReport(onboarded=["alpha-mcp"])

    assert asyncio.run(refresh_serving_catalog(mux, report, ["alpha-mcp"])) is False
    assert mux.refreshes == 1


def test_service_pass_publishes_its_report_to_multiplexer_status(
    eg: _Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        onboarding, "configured_fleet_endpoints", lambda **_kwargs: (ALPHA,)
    )
    mux = _Mux({})
    service = FleetOnboardingService(onboarding=_onboarding(eg), multiplexer=mux)

    report = _run_in_session(service.run_pass())

    assert report.onboarded == ["alpha-mcp"]
    assert mux.refreshes == 1
    assert mux._fleet_onboarding == report.as_dict()


def test_service_runs_once_then_per_interval_until_stopped(eg: _Registry) -> None:
    stop = threading.Event()
    service = FleetOnboardingService(
        onboarding=_onboarding(eg), multiplexer=_Mux({}), interval_s=0, stop=stop
    )
    passes = service.passes()

    next(passes)
    next(passes)
    stop.set()
    assert next(passes, "stopped") == "stopped"


def test_a_failing_pass_never_escapes_the_boot_thread(
    eg: _Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_kwargs: Any) -> Any:
        raise ValueError("MCP catalog must be an object")

    monkeypatch.setattr(onboarding, "configured_fleet_endpoints", broken)
    service = FleetOnboardingService(onboarding=_onboarding(eg), multiplexer=_Mux({}))

    service._run_pass_safely()

    assert eg.registers == []


def test_boot_without_an_engine_starts_no_thread() -> None:
    started = start_fleet_onboarding(
        engine=SimpleNamespace(), session=_session(), multiplexer=_Mux({})
    )

    assert started is None


def test_cli_runs_one_full_pass_and_fails_on_any_server(
    eg: _Registry,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from graph_os.mcp_server import bootstrap, runtime

    async def capture(endpoint: FleetEndpoint, *, auth: Any = None) -> Any:
        if endpoint is BETA:
            raise ConnectionError(f"{endpoint.url} refused the session")
        return await _served_pack(endpoint, auth=auth)

    monkeypatch.setattr(runtime, "_mint_process_session", lambda _t: _session())
    monkeypatch.setattr(runtime, "_get_engine", lambda: eg)
    monkeypatch.setattr(bootstrap, "_wait_for_engine_materialization", lambda e: None)
    monkeypatch.setattr(
        onboarding, "configured_fleet_endpoints", lambda **_kwargs: (ALPHA, BETA)
    )
    monkeypatch.setattr(onboarding, "capture_server_pack", capture)

    code = production_ops.main(["onboard-fleet", "--served-url", SELF_URL])

    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["operation"] == "onboard-fleet" and report["ok"] is False
    assert report["onboarded"] == ["alpha-mcp"]
    # Failure detail can carry an endpoint; the CLI prints only its type.
    assert report["failed"] == {"beta-mcp": "ConnectionError"}
