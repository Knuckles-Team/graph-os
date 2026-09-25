"""The process host contributes only verified and configured API authorities."""

from __future__ import annotations

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
