"""GraphOS is the sole authenticated composition root for SDK runners."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_connector_sdk.credentials.references import SecretReference
from agent_connector_sdk.runner.descriptors import RunnerSettings
from agent_connector_sdk.sinks.epistemic_graph import PackImportAuthorityResolver
from agent_utilities.knowledge_graph.core.session import GraphSession, use_session
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext
from epistemic_graph.generated.connector_pack import (
    AgentLibraryMutationContext,
    McpCatalogSnapshotBinding,
)

from graph_os.connector_runner import (
    ConnectorRunnerComposition,
    ConnectorRunnerConfig,
    ConnectorRunnerNotReadyError,
    compose_mounted_connector_runner,
    run_connector_sync,
)
from graph_os.epistemic import AuthorityError, ClientContext, ConnectClient


class Resolver:
    def __init__(self, value: str = "engine-secret") -> None:
        self.value = value
        self.seen: list[SecretReference] = []

    def resolve(self, reference: SecretReference) -> str:
        self.seen.append(reference)
        return self.value


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


class Sink:
    def __init__(self, *, ready: bool = True, reason: str | None = None) -> None:
        self._readiness = SimpleNamespace(ready=ready, reason=reason)

    async def readiness(self) -> Any:
        return self._readiness


def config(tmp_path: Path, **overrides: object) -> ConnectorRunnerConfig:
    values: dict[str, object] = {
        "graph": "tenant-a:connectors",
        "state_dir": tmp_path,
        "auth_secret_ref": "env://GRAPH_SERVICE_AUTH_SECRET",
        "socket_path": "/run/epistemic-graph.sock",
    }
    values.update(overrides)
    return ConnectorRunnerConfig(**values)  # type: ignore[arg-type]


def context(
    *,
    scopes: tuple[str, ...] = (
        "source:ingest",
        "agent:pack-control",
        "agent:pack-read",
        "blob:write",
        "blob:read",
        "connector:catalog-attest",
        "admin:connector-pack",
    ),
) -> ClientContext:
    return ClientContext(
        principal="service:graph-os",
        tenant="tenant-a",
        audience="epistemic-graph",
        agent_id="service:graph-os",
        roles=("connector-runner",),
        scopes=scopes,
        policy_version="policy-7",
    )


async def pack_import_authority(_connector: str) -> Any:
    raise AssertionError("the SDK invokes the resolver only during pack import")


PACK_AUTHORITY = cast(PackImportAuthorityResolver, pack_import_authority)


def graph_session() -> GraphSession:
    return GraphSession(
        actor=ActorContext(
            actor_id="service:graph-os",
            actor_type=ActorType.AUTOMATED_SERVICE,
            tenant_id="tenant-a",
            authenticated=True,
        ),
        tenant="tenant-a",
        scopes=frozenset(context().scopes),
        graph="tenant-a:connectors",
        audience="graph-os",
        policy_version="policy-7",
    )


def test_mounted_runner_factory_requires_same_verified_session_and_authority(
    tmp_path: Path,
) -> None:
    class Mounted:
        def __init__(self, *, ready: bool) -> None:
            self.ready = ready

        def connector_pack_authority_ready(self) -> bool:
            return self.ready

        async def resolve_connector_pack_authority(self, connector: str) -> Any:
            return await pack_import_authority(connector)

    session = graph_session()
    mounted = Mounted(ready=True)
    with use_session(session):
        composition = compose_mounted_connector_runner(
            config(tmp_path),
            session=session,
            context=context(),
            multiplexer=mounted,
            credential_resolver=Resolver(),
        )
        assert composition._pack_import_authority.__self__ is mounted
        with pytest.raises(ConnectorRunnerNotReadyError, match="mounted"):
            compose_mounted_connector_runner(
                config(tmp_path),
                session=session,
                context=context(),
                multiplexer=Mounted(ready=False),
                credential_resolver=Resolver(),
            )
        with pytest.raises(ConnectorRunnerNotReadyError, match="identity"):
            compose_mounted_connector_runner(
                config(tmp_path),
                session=session,
                context=ClientContext(
                    principal="service:other",
                    tenant="tenant-a",
                    audience="epistemic-graph",
                    agent_id="service:other",
                    roles=("connector-runner",),
                    scopes=context().scopes,
                    policy_version="policy-7",
                ),
                multiplexer=mounted,
                credential_resolver=Resolver(),
            )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"socket_path": None}, "exactly one"),
        ({"tcp_addr": "eg.example.invalid:9737"}, "exactly one"),
        ({"auth_secret_ref": "literal-secret"}, "secret reference"),
    ],
)
def test_config_refuses_missing_ambiguous_or_literal_auth(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        config(tmp_path, **overrides)


def test_composition_requires_dynamic_pack_import_authority(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="pack_import_authority is required"):
        ConnectorRunnerComposition(
            config(tmp_path),
            credential_resolver=Resolver(),
            pack_import_authority=cast(PackImportAuthorityResolver, None),
        )


@pytest.mark.asyncio
async def test_injects_exact_authenticated_client_and_verified_context(
    tmp_path: Path,
) -> None:
    resolver = Resolver()
    connected: list[dict[str, Any]] = []
    client = Client()
    built: list[dict[str, Any]] = []

    async def connect(**kwargs: Any) -> Client:
        connected.append(kwargs)
        return client

    def services_factory(settings: RunnerSettings, **kwargs: Any) -> Any:
        built.append({"settings": settings, **kwargs})
        return SimpleNamespace(sink=Sink())

    composition = ConnectorRunnerComposition(
        config(tmp_path),
        credential_resolver=resolver,
        pack_import_authority=PACK_AUTHORITY,
        connect=cast(ConnectClient, connect),
        services_factory=services_factory,
    )
    settings = RunnerSettings(max_concurrency=2)
    verified = context()

    async with composition.services(verified, settings) as services:
        assert services.sink is not None

    assert resolver.seen == [
        SecretReference(scheme="env", target="GRAPH_SERVICE_AUTH_SECRET")
    ]
    assert connected == [
        {
            "graph_name": "tenant-a:connectors",
            "verified_context": verified.to_claims(),
            "socket_path": "/run/epistemic-graph.sock",
            "tcp_addr": None,
            "auth_secret": "engine-secret",
        }
    ]
    assert client.contexts == [verified.to_claims()]
    assert len(built) == 1
    assert built[0]["settings"] == settings
    assert built[0]["state_dir"] == tmp_path
    assert built[0]["sink_name"] == "epistemic_graph"
    assert built[0]["sink_client"] is client
    assert callable(built[0]["pack_import_authority"])
    assert built[0]["policy"] is None
    await composition.close()
    assert client.closed is True


@pytest.mark.asyncio
async def test_refuses_raw_claims_and_missing_ingest_scope_before_connect(
    tmp_path: Path,
) -> None:
    calls = 0

    async def connect(**_kwargs: Any) -> Client:
        nonlocal calls
        calls += 1
        return Client()

    composition = ConnectorRunnerComposition(
        config(tmp_path),
        credential_resolver=Resolver(),
        pack_import_authority=PACK_AUTHORITY,
        connect=cast(ConnectClient, connect),
    )
    with pytest.raises(TypeError, match="verified ClientContext"):
        async with composition.services(
            cast(ClientContext, context().to_claims()), RunnerSettings()
        ):
            pass
    with pytest.raises(AuthorityError, match="source:ingest"):
        async with composition.services(
            context(scopes=("graph:read",)), RunnerSettings()
        ):
            pass
    with pytest.raises(AuthorityError, match="agent:pack-control"):
        async with composition.services(
            context(scopes=("source:ingest",)), RunnerSettings()
        ):
            pass
    # The remote runner is an importer. Catalog attestation belongs only to
    # the mounted GraphOS process, never to this separate request principal.
    authorized = context(
        scopes=(
            "source:ingest",
            "agent:pack-control",
            "agent:pack-read",
            "blob:write",
            "blob:read",
        )
    )
    assert composition._verified_claims(authorized)["tenant"] == "tenant-a"
    assert calls == 0


@pytest.mark.asyncio
async def test_refuses_unready_injected_sink_without_fallback(tmp_path: Path) -> None:
    async def connect(**_kwargs: Any) -> Client:
        return Client()

    def services_factory(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            sink=Sink(ready=False, reason="ConnectorPack contract unavailable")
        )

    composition = ConnectorRunnerComposition(
        config(tmp_path),
        credential_resolver=Resolver(),
        pack_import_authority=PACK_AUTHORITY,
        connect=cast(ConnectClient, connect),
        services_factory=services_factory,
    )
    with pytest.raises(
        ConnectorRunnerNotReadyError, match="ConnectorPack contract unavailable"
    ):
        async with composition.services(context(), RunnerSettings()):
            pass


@pytest.mark.asyncio
async def test_tenant_graph_binding_is_not_reused_across_tenants(
    tmp_path: Path,
) -> None:
    async def connect(**_kwargs: Any) -> Client:
        return Client()

    def services_factory(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(sink=Sink())

    composition = ConnectorRunnerComposition(
        config(tmp_path),
        credential_resolver=Resolver(),
        pack_import_authority=PACK_AUTHORITY,
        connect=cast(ConnectClient, connect),
        services_factory=services_factory,
    )
    async with composition.services(context(), RunnerSettings()):
        pass

    other = ClientContext(
        principal="service:graph-os",
        tenant="tenant-b",
        audience="epistemic-graph",
        agent_id="service:graph-os",
        roles=("connector-runner",),
        scopes=context().scopes,
        policy_version="policy-7",
    )
    with pytest.raises(AuthorityError, match="another tenant"):
        async with composition.services(other, RunnerSettings()):
            pass


@pytest.mark.asyncio
async def test_runner_entry_uses_verified_client_and_closes_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import graph_os.connector_runner as module

    runner_config = tmp_path / "runner.yml"
    runner_config.write_text("connectors: []\n")
    client = Client()
    built: list[dict[str, Any]] = []

    async def connect(**_kwargs: Any) -> Client:
        return client

    def services_factory(_settings: RunnerSettings, **kwargs: Any) -> Any:
        built.append(kwargs)
        return SimpleNamespace(sink=Sink())

    async def run(argv: list[str], services: Any) -> int:
        assert argv == [
            "--config",
            str(runner_config),
            "--log-format",
            "json",
            "--once",
        ]
        assert services.sink is not None
        assert client.closed is False
        return 0

    monkeypatch.setattr(module, "run_with_services", run)
    composition = ConnectorRunnerComposition(
        config(tmp_path),
        credential_resolver=Resolver(),
        pack_import_authority=PACK_AUTHORITY,
        connect=cast(ConnectClient, connect),
        services_factory=services_factory,
    )
    assert (
        await run_connector_sync(
            runner_config, composition=composition, context=context(), once=True
        )
        == 0
    )
    assert built[0]["sink_client"] is client
    assert callable(built[0]["pack_import_authority"])
    assert client.closed is True


def _binding(**changes: object) -> McpCatalogSnapshotBinding:
    values: dict[str, object] = {
        "configuration_revision": 7,
        "catalog_generation": 11,
        "child_connection_generation": 3,
        "snapshot_digest": "a" * 64,
        "authorization_scope_digest": "b" * 64,
    }
    values.update(changes)
    return McpCatalogSnapshotBinding.model_validate(values)


def _mutation(**changes: object) -> AgentLibraryMutationContext:
    opaque_caller = (
        "principal:sha256:" + hashlib.sha256(b"service:graph-os").hexdigest()
    )
    values: dict[str, object] = {
        "request_id": 1,
        "principal": "service:graph-os",
        "caller_principal": opaque_caller,
        "attempt_nonce": "07" * 32,
        "tenant_id": "tenant-a",
        "actor_scope": "agent:pack-control",
        "purpose_id": "connector-import",
        "policy_revision": "policy-7",
        "policy_digest": "sha256:" + "c" * 64,
        "policy_decision_id": "decision-1",
        "idempotency_key": "connector-import-1",
        "created_at_ms": 1_700_000_000_000,
    }
    values.update(changes)
    return AgentLibraryMutationContext.model_validate(values)


@pytest.mark.asyncio
async def test_pack_authority_requires_exact_live_catalog_fields_and_identity(
    tmp_path: Path,
) -> None:
    offered: list[object] = [(_binding(), _mutation())]

    async def authority(_connector: str) -> Any:
        return offered[0]

    composition = ConnectorRunnerComposition(
        config(tmp_path),
        credential_resolver=Resolver(),
        pack_import_authority=cast(PackImportAuthorityResolver, authority),
    )
    resolver = composition._bound_pack_authority(context())
    assert await resolver("demo-agent") == offered[0]
    for wrong in (
        (_binding(configuration_revision=0), _mutation()),
        (_binding(catalog_generation=0), _mutation()),
        (_binding(child_connection_generation=0), _mutation()),
        (_binding(snapshot_digest="0" * 64), _mutation()),
        (_binding(authorization_scope_digest="0" * 64), _mutation()),
        (_binding(), _mutation(tenant_id="other")),
        (_binding(), _mutation(caller_principal="other")),
        (_binding(), _mutation(caller_principal="service:graph-os")),
        None,
    ):
        offered[0] = wrong
        with pytest.raises(ConnectorRunnerNotReadyError, match="pack authority"):
            await resolver("demo-agent")
    await composition.close()
