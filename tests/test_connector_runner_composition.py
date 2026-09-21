"""GraphOS is the sole authenticated composition root for SDK runners."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_connector_sdk.credentials.references import SecretReference
from agent_connector_sdk.runner.descriptors import RunnerSettings
from agent_connector_sdk.sinks.epistemic_graph import PackImportAuthorityResolver

from graph_os.connector_runner import (
    ConnectorRunnerComposition,
    ConnectorRunnerConfig,
    ConnectorRunnerNotReadyError,
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


def context(*, scopes: tuple[str, ...] = ("source:ingest",)) -> ClientContext:
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
    assert built == [
        {
            "settings": settings,
            "state_dir": tmp_path,
            "sink_name": "epistemic_graph",
            "sink_client": client,
            "pack_import_authority": PACK_AUTHORITY,
            "policy": None,
        }
    ]
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
        scopes=("source:ingest",),
        policy_version="policy-7",
    )
    with pytest.raises(AuthorityError, match="another tenant"):
        async with composition.services(other, RunnerSettings()):
            pass
