"""The process host contributes only verified and configured API authorities."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from graph_os.api import host_bootstrap
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.policy import PolicyGate


@pytest.mark.asyncio
async def test_host_runtime_uses_process_graph_route_and_configured_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.core import config as config_module

    from graph_os.api.host_secrets import HostSecretPorts
    from graph_os.mcp_server import runtime

    monkeypatch.setattr(
        host_bootstrap,
        "host_secret_ports_from_config",
        lambda: HostSecretPorts(b"k" * 32, "env://CONTEXT_TOKEN", lambda ref: None),
    )
    monkeypatch.setattr(config_module, "setting", lambda key: "embedded")
    calls: list[str] = []
    client = SimpleNamespace(use_verified_context=lambda claims: None)
    monkeypatch.setattr(
        runtime,
        "graph_client",
        lambda tenant: (calls.append(tenant), client)[1],
    )
    monkeypatch.setattr(
        PolicyGate,
        "from_config",
        classmethod(
            lambda cls, config, identity_mode, configured_mode=None: (
                calls.append(f"policy:{identity_mode}:{configured_mode}"),
                PolicyGate("none"),
            )[1]
        ),
    )
    inputs = host_bootstrap.HostRuntimeInputs(
        identity_mode="local",
        service_claims=lambda tenant: {},
        check_access=lambda *args: None,
        service_scopes=frozenset(),
        plan_client=object(),
        fleet_gateway=object(),
        fleet_search=lambda **kwargs: (),
        fleet_ops_factory=lambda *args: None,
        resolver=IntentResolver(),
        bindings={},
    )
    assembled = host_bootstrap.host_runtime_authorities(inputs)
    assert assembled.plan_seal_key == b"k" * 32
    assert assembled.bearer_ref == "env://CONTEXT_TOKEN"
    assert await assembled.caller_client("tenant:one") is client
    assert await assembled.service_client("tenant:two") is client
    assert calls == ["policy:local:embedded", "tenant:one", "tenant:two"]
    with pytest.raises(ValueError, match="tenant graph"):
        await assembled.caller_client("")


def test_missing_host_inputs_refuse_assembly() -> None:
    with pytest.raises(TypeError, match="complete host runtime inputs"):
        host_bootstrap.host_runtime_authorities(None)


@pytest.mark.asyncio
async def test_process_ports_preserve_verified_tenant_and_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.mcp_server import runtime

    seen: list[tuple[str, object]] = []
    claims = {
        "principal": "svc:graph-os",
        "tenant": "tenant-a",
        "scopes": ["lease:write", "security:check"],
    }

    class Session:
        tenant = "tenant-a"
        actor = SimpleNamespace(actor_id="svc:graph-os")

        def engine_verified_context(self) -> dict[str, object]:
            return dict(claims)

    class Leases:
        async def issue(self, **kwargs):
            seen.append(("issue", kwargs["tenant"]))
            return {"outcome": "issued"}

    class Client:
        control_leases = Leases()

        @contextmanager
        def use_verified_context(self, context):
            seen.append(("claims", context["principal"]))
            yield

        async def check_access(self, agent_id, access, *, graph):
            seen.append(("check", (agent_id, access, graph)))
            return True

    client = Client()
    monkeypatch.setattr(runtime, "graph_client", lambda tenant: client)
    process = Session()
    inputs = host_bootstrap.verified_process_inputs(
        process,
        identity_mode="oidc",
        fleet_gateway=object(),
        fleet_search=lambda **kwargs: (),
        fleet_ops_factory=lambda *args: None,
        resolver=IntentResolver(),
        bindings={},
    )
    assert inputs.service_scopes == frozenset({"lease:write", "security:check"})
    assert inputs.service_claims("tenant-a")["principal"] == "svc:graph-os"
    with pytest.raises(PermissionError, match="no authority"):
        inputs.service_claims("tenant-b")
    assert await inputs.plan_client.control_leases.issue(tenant="tenant-a") == {
        "outcome": "issued"
    }
    with pytest.raises(PermissionError, match="authority"):
        await inputs.plan_client.control_leases.issue(tenant="tenant-b")
    caller = SimpleNamespace(
        session=SimpleNamespace(
            tenant="tenant-a", actor=SimpleNamespace(actor_id="user:one")
        ),
        tenant="tenant-a",
        principal="user:one",
    )
    assert await inputs.check_access(client, caller, "graph:owned") is True
    assert seen == [
        ("claims", "svc:graph-os"),
        ("issue", "tenant-a"),
        ("check", ("user:one", "read", "graph:owned")),
    ]


def test_process_ports_reject_non_service_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(
        engine_verified_context=lambda: {
            "principal": "user:one",
            "tenant": "tenant-a",
            "scopes": ["lease:write"],
        }
    )
    with pytest.raises(PermissionError, match="service identity"):
        host_bootstrap.verified_process_inputs(
            session,
            identity_mode="oidc",
            fleet_gateway=object(),
            fleet_search=lambda **kwargs: (),
            fleet_ops_factory=lambda *args: None,
            resolver=IntentResolver(),
            bindings={},
        )


def test_process_host_composition_shares_policy_and_verified_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.core import config as config_module

    from graph_os.fleet.catalog_reader import DeferredFleetCatalogReader
    from graph_os.fleet.gateway_ops import FleetGateway
    from graph_os.mcp_server import runtime

    gate = PolicyGate("none")
    monkeypatch.setattr(
        PolicyGate,
        "from_config",
        classmethod(lambda cls, *args, **kwargs: gate),
    )
    monkeypatch.setattr(config_module, "setting", lambda key: "none")
    monkeypatch.setattr(
        runtime,
        "graph_client",
        lambda tenant: SimpleNamespace(use_verified_context=lambda claims: None),
    )
    session = SimpleNamespace(
        engine_verified_context=lambda: {
            "principal": "svc:graph-os",
            "tenant": "tenant-a",
            "scopes": ["mcp:discover"],
        }
    )
    reader = DeferredFleetCatalogReader()

    async def sdk_entries():
        return ()

    inputs = host_bootstrap.compose_process_host_inputs(
        session,
        identity_mode="oidc",
        fleet_reader=reader,
        sdk_entries=sdk_entries,
        bindings={},
    )
    assert inputs.policy_gate is gate
    assert isinstance(inputs.fleet_gateway, FleetGateway)
    assert callable(inputs.fleet_search)
    assert callable(inputs.fleet_ops_factory)
