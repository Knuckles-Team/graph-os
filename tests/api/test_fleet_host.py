"""The served fleet uses one guarded multiplexer and no pre-attach catalog."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.errors import EngineRefusal, FleetRefusal
from graph_os.api.fleet_host import _native_refusal, compose_fleet_host_ports
from graph_os.api.generated.engine_errors import ENGINE_ERRORS
from graph_os.api.policy import PolicyGate


@pytest.mark.asyncio
async def test_fleet_host_refuses_before_attach_and_binds_once() -> None:
    class Reader:
        async def read(self):
            return SimpleNamespace(servers=())

    async def sdk_entries():
        return ()

    ports = compose_fleet_host_ports(
        reader=Reader(), sdk_entries=sdk_entries, policy_gate=PolicyGate("none")
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:discover"}),
        principal="user:one",
        tenant="tenant-a",
        principal_kind="human",
        authenticated=True,
        delegated=False,
        session=SimpleNamespace(
            tenant="tenant-a", actor=SimpleNamespace(actor_id="user:one")
        ),
    )
    with pytest.raises(RuntimeError, match="not attached"):
        await ports.search(caller=caller, query="check")
    with pytest.raises(RuntimeError, match="not attached"):
        await ports.gateway.search(caller, query="check")

    mux = SimpleNamespace(_probe_cache={}, status_snapshot=lambda: {"children": {}})

    async def mount(item, forwarder):
        raise AssertionError("no fleet items should be mounted")

    async def notify(session_key):
        return True

    ops = ports.ops_factory(mux, mount, notify, lambda item: item.name)
    assert await ports.search(caller=caller, query="check") == []
    assert await ports.gateway.search(caller, query="check") == {
        "items": [],
        "next_cursor": None,
    }
    assert ops is not None
    with pytest.raises(RuntimeError, match="already attached"):
        ports.ops_factory(mux, mount, notify, lambda item: item.name)


def test_fleet_host_requires_real_reader_sdk_and_policy() -> None:
    async def sdk_entries():
        return ()

    with pytest.raises(ValueError, match="EG fleet catalog"):
        compose_fleet_host_ports(
            reader=None, sdk_entries=sdk_entries, policy_gate=PolicyGate("none")
        )
    with pytest.raises(ValueError, match="SDK pack reader"):
        compose_fleet_host_ports(
            reader=SimpleNamespace(read=lambda: None),
            sdk_entries=None,
            policy_gate=PolicyGate("none"),
        )


@pytest.mark.parametrize(
    ("refusal", "source", "code"),
    (
        (
            FleetRefusal("CHILD_REFUSED", "server-one", "tool-one"),
            "fleet",
            "CHILD_REFUSED",
        ),
        (EngineRefusal(sorted(ENGINE_ERRORS)[0]), "engine", sorted(ENGINE_ERRORS)[0]),
    ),
)
def test_native_refusal_preserves_mcp_error_and_structured_code(
    refusal, source: str, code: str
) -> None:
    result = _native_refusal(
        refusal,
        caller=SimpleNamespace(request_id="request-one"),
        registry_digest="a" * 64,
    ).to_mcp_result()
    assert result.isError is True
    assert result.structuredContent["error"]["source"] == source
    assert result.structuredContent["error"]["code"] == code
    assert result.content[0].text == "GraphOS operation refused"
