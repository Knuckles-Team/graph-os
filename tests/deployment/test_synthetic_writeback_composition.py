"""The served D18 fixture receives only the verified GraphOS EG authority."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from agent_connector_sdk.ports.session import TransportEndpoint
from agent_connector_sdk.testing.synthetic_writeback import (
    LiveSyntheticWriteBackEvidence,
)
from agent_connector_sdk.testing.writeback import make_writeback_fixture
from agent_connector_sdk.writeback.durable_transport import FileWriteBackTransport
from epistemic_graph.generated.write_back import WriteBackAuthorizationMode

from graph_os.connector_runner import (
    ConnectorRunnerConfig,
    ConnectorRunnerNotReadyError,
)
from graph_os.deployment import synthetic_writeback as wiring
from graph_os.epistemic import AuthorityError, ClientContext, ConnectClient


class Resolver:
    def resolve(self, _reference: object) -> str:
        return "fixture-eg-secret"


class Client:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []
        self.closed = False

    @contextmanager
    def use_verified_context(self, context: dict[str, Any]):
        self.contexts.append(context)
        yield

    async def close(self) -> None:
        self.closed = True


def _context(
    *, tenant: str = "tenant-a", scopes: tuple[str, ...] = (wiring.WRITE_BACK_SCOPE,)
) -> ClientContext:
    return ClientContext(
        principal="service:graph-os",
        tenant=tenant,
        audience="epistemic-graph",
        agent_id="service:graph-os",
        roles=("connector-writer",),
        scopes=scopes,
        policy_version="policy-7",
    )


def _composition(
    tmp_path: Path, connect: ConnectClient
) -> wiring.SyntheticWriteBackComposition:
    return wiring.SyntheticWriteBackComposition(
        ConnectorRunnerConfig(
            graph="tenant-a:writeback",
            state_dir=tmp_path,
            auth_secret_ref="env://GRAPH_SERVICE_AUTH_SECRET",
            socket_path="/run/eg.sock",
        ),
        credential_resolver=cast(Any, Resolver()),
        connect=connect,
    )


@pytest.mark.asyncio
async def test_serve_and_probe_inject_same_verified_client_and_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected: list[dict[str, Any]] = []
    client = Client()

    async def connect(**kwargs: Any) -> Client:
        connected.append(kwargs)
        return client

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def serve(*args: Any, **kwargs: Any) -> None:
        calls.append(("serve", args, kwargs))

    async def probe(*args: Any, **kwargs: Any) -> LiveSyntheticWriteBackEvidence:
        calls.append(("probe", args, kwargs))
        return evidence

    monkeypatch.setattr(wiring, "serve_synthetic_writeback_test_namespace", serve)
    monkeypatch.setattr(wiring, "run_live_synthetic_writeback_acceptance", probe)
    composition = _composition(tmp_path, cast(ConnectClient, connect))
    verified = _context()
    evidence = LiveSyntheticWriteBackEvidence(
        tenant_id="tenant-a",
        change_set_id="change-1",
        change_set_digest="digest",
        receipt_id="receipt-1",
        receipt_sequence=1,
        post_source_version="v2",
        server_instance_id="server-before",
    )
    endpoint = TransportEndpoint(
        url="https://connector.example.test/mcp", bearer_token="test-token"
    )
    await composition.serve(
        verified,
        tmp_path / "source",
        client_id="test-client",
        command_args=["--transport", "streamable-http"],
    )
    result = await composition.probe(
        verified,
        endpoint,
        change_set_id="change-1",
        expected_mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
    )
    assert result is evidence
    assert connected == [
        {
            "graph_name": "tenant-a:writeback",
            "verified_context": verified.to_claims(),
            "socket_path": "/run/eg.sock",
            "tcp_addr": None,
            "auth_secret": "fixture-eg-secret",
        }
    ]
    assert client.contexts == [verified.to_claims(), verified.to_claims()]
    assert calls[0][1][0] is client
    assert calls[0][2]["tenant_id"] == "tenant-a"
    assert calls[0][2]["client_id"] == "test-client"
    assert calls[1][1] == (client, endpoint)
    assert calls[1][2]["graph"] == "tenant-a:writeback"
    assert calls[1][2]["expected_mode"] is WriteBackAuthorizationMode.PROPOSAL_APPROVAL
    await composition.close()
    assert client.closed


@pytest.mark.asyncio
async def test_unverified_or_ungranted_context_refuses_before_connect(
    tmp_path: Path,
) -> None:
    connects = 0

    async def connect(**_kwargs: Any) -> Client:
        nonlocal connects
        connects += 1
        return Client()

    composition = _composition(tmp_path, cast(ConnectClient, connect))
    with pytest.raises(TypeError, match="verified ClientContext"):
        await composition.serve(
            cast(ClientContext, {}), tmp_path, client_id="x", command_args=[]
        )
    with pytest.raises(AuthorityError, match="connector:write-back"):
        await composition.serve(
            _context(scopes=("source:ingest",)),
            tmp_path,
            client_id="x",
            command_args=[],
        )
    assert connects == 0


def test_namespace_tls_requires_tcp_and_server_name(tmp_path: Path) -> None:
    config = ConnectorRunnerConfig(
        graph="tenant-a:writeback",
        state_dir=tmp_path,
        auth_secret_ref="env://GRAPH_SERVICE_AUTH_SECRET",
        tcp_addr="engine.test:9100",
    )
    with pytest.raises(ValueError, match="server hostname"):
        wiring.SyntheticWriteBackComposition(
            config, credential_resolver=cast(Any, Resolver()), tls=True
        )
    with pytest.raises(ValueError, match="requires TLS"):
        wiring.SyntheticWriteBackComposition(
            config,
            credential_resolver=cast(Any, Resolver()),
            tls_server_hostname="engine.test",
        )


@pytest.mark.asyncio
async def test_evidence_from_another_tenant_refuses_before_connect(
    tmp_path: Path,
) -> None:
    connects = 0

    async def connect(**_kwargs: Any) -> Client:
        nonlocal connects
        connects += 1
        return Client()

    composition = _composition(tmp_path, cast(ConnectClient, connect))
    evidence = LiveSyntheticWriteBackEvidence(
        tenant_id="tenant-b",
        change_set_id="change-1",
        change_set_digest="digest",
        receipt_id="receipt-1",
        receipt_sequence=1,
        post_source_version="v2",
        server_instance_id="server-before",
    )
    with pytest.raises(ValueError, match="another tenant"):
        await composition.verify_source_effect(_context(), tmp_path, evidence)
    assert connects == 0


@pytest.mark.asyncio
async def test_seed_source_uses_eg_grant_and_refuses_wrong_mode(tmp_path: Path) -> None:
    change = make_writeback_fixture().change_set.model_copy(
        update={
            "tenant_id": "tenant-a",
            "connector_id": "synthetic-writeback",
            "source_instance_id": "synthetic-source",
        }
    )
    change = change.model_copy(update={"change_set_digest": change.canonical_digest()})

    class GrantClient(Client):
        async def _send(
            self,
            method: str,
            params: dict[str, object],
            graph: str | None,
            *,
            idempotency_key: str | None = None,
        ) -> object:
            assert method == "WriteBack" and graph == "tenant-a:writeback"
            assert params["op"] == {
                "op": "get",
                "tenant_id": "tenant-a",
                "change_set_id": "change-1",
            }
            assert idempotency_key is None
            return change.model_dump(mode="json")

    client = GrantClient()

    async def connect(**_kwargs: Any) -> GrantClient:
        return client

    composition = _composition(tmp_path, cast(ConnectClient, connect))
    source = tmp_path / "source"
    with pytest.raises(ConnectorRunnerNotReadyError, match="not granted"):
        await composition.seed_source(
            _context(),
            source,
            change_set_id="change-1",
            expected_mode=WriteBackAuthorizationMode.STANDING_POLICY,
            initial_fields={"status": "new"},
        )
    assert not (source / "state.json").exists()
    seeded = await composition.seed_source(
        _context(),
        source,
        change_set_id="change-1",
        expected_mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
        initial_fields={"status": "new"},
    )
    assert seeded == change
    current = await FileWriteBackTransport(source).read_current(change)
    assert current.fields == {"status": "new"}
    assert current.source_version == "v1"


@pytest.mark.asyncio
async def test_restart_acceptance_requires_new_instance_then_verifies_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def connect(**_kwargs: Any) -> Client:
        raise AssertionError("the probe stubs own their EG client")

    composition = _composition(tmp_path, cast(ConnectClient, connect))
    evidence = LiveSyntheticWriteBackEvidence(
        tenant_id="tenant-a",
        change_set_id="change-1",
        change_set_digest="digest",
        receipt_id="receipt-1",
        receipt_sequence=1,
        post_source_version="v2",
        server_instance_id="server-before",
    )
    events: list[str] = []
    instance = "pod-before"

    async def identity() -> str:
        events.append(f"identity:{instance}")
        return instance

    async def restart() -> None:
        nonlocal instance
        events.append("restart")
        instance = "pod-after"

    async def probe(
        *_args: Any, prior: LiveSyntheticWriteBackEvidence | None = None, **_kwargs: Any
    ) -> LiveSyntheticWriteBackEvidence:
        events.append("replay" if prior else "apply")
        assert prior is None or prior == evidence
        return (
            evidence.model_copy(update={"server_instance_id": "server-after"})
            if prior
            else evidence
        )

    async def verify(*_args: Any) -> None:
        events.append("verify-source")

    monkeypatch.setattr(composition, "probe", probe)
    monkeypatch.setattr(composition, "verify_source_effect", verify)
    result = await composition.run_restarted_acceptance(
        _context(),
        TransportEndpoint(
            url="https://connector.example.test/mcp", bearer_token="test-token"
        ),
        tmp_path / "source",
        change_set_id="change-1",
        expected_mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
        server_instance=identity,
        restart_server=restart,
    )
    assert result == wiring.RestartedWriteBackEvidence(
        "pod-before",
        "pod-after",
        evidence.model_copy(update={"server_instance_id": "server-after"}),
    )
    assert events == [
        "identity:pod-before",
        "apply",
        "restart",
        "identity:pod-after",
        "replay",
        "verify-source",
    ]


@pytest.mark.asyncio
async def test_restart_acceptance_refuses_reused_instance_before_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def connect(**_kwargs: Any) -> Client:
        raise AssertionError("the probe stub owns its EG client")

    composition = _composition(tmp_path, cast(ConnectClient, connect))
    probes = 0

    async def probe(*_args: Any, **_kwargs: Any) -> LiveSyntheticWriteBackEvidence:
        nonlocal probes
        probes += 1
        return LiveSyntheticWriteBackEvidence(
            tenant_id="tenant-a",
            change_set_id="change-1",
            change_set_digest="digest",
            receipt_id="receipt-1",
            receipt_sequence=1,
            post_source_version="v2",
            server_instance_id="server-before",
        )

    async def identity() -> str:
        return "same-pod"

    async def restart() -> None:
        pass

    monkeypatch.setattr(composition, "probe", probe)
    with pytest.raises(ConnectorRunnerNotReadyError, match="restart was not observed"):
        await composition.run_restarted_acceptance(
            _context(),
            TransportEndpoint(
                url="https://connector.example.test/mcp", bearer_token="test-token"
            ),
            tmp_path / "source",
            change_set_id="change-1",
            expected_mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
            server_instance=identity,
            restart_server=restart,
        )
    assert probes == 1


@pytest.mark.asyncio
async def test_restart_acceptance_refuses_stale_mcp_route_after_pod_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def connect(**_kwargs: Any) -> Client:
        raise AssertionError("the probe stub owns its EG client")

    composition = _composition(tmp_path, cast(ConnectClient, connect))
    evidence = LiveSyntheticWriteBackEvidence(
        tenant_id="tenant-a",
        change_set_id="change-1",
        change_set_digest="digest",
        receipt_id="receipt-1",
        receipt_sequence=1,
        post_source_version="v2",
        server_instance_id="still-serving-old-pod",
    )
    pod = "pod-before"
    probes = 0

    async def identity() -> str:
        return pod

    async def restart() -> None:
        nonlocal pod
        pod = "pod-after"

    async def probe(*_args: Any, **_kwargs: Any) -> LiveSyntheticWriteBackEvidence:
        nonlocal probes
        probes += 1
        return evidence

    monkeypatch.setattr(composition, "probe", probe)
    with pytest.raises(ConnectorRunnerNotReadyError, match="still serves"):
        await composition.run_restarted_acceptance(
            _context(),
            TransportEndpoint(
                url="https://connector.example.test/mcp", bearer_token="test-token"
            ),
            tmp_path / "source",
            change_set_id="change-1",
            expected_mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
            server_instance=identity,
            restart_server=restart,
        )
    assert probes == 2
