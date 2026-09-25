"""EH-627: exact scopes and principal rules precede narrowing Eunomia decisions."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from eunomia_core import schemas

from graph_os.api.policy import PolicyGate, PolicyUnavailable, fleet_resource
from graph_os.api.policy.cache import DecisionCache
from graph_os.api.policy.defaults import policy_mode


@dataclass
class Caller:
    principal: str = "usr:alice"
    tenant: str = "tenant-a"
    principal_kind: str = "human"
    effective_scopes: frozenset[str] = frozenset(
        {"mcp:discover", "mcp:delegate", "read:x"}
    )
    authenticated: bool = True
    delegated: bool = False
    policy_revision: str = "rev-1"


class PDP:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.requests: list[schemas.CheckRequest] = []
        self.fail = False

    async def bulk_check(self, requests):
        if self.fail:
            raise ConnectionError("offline")
        self.requests.extend(requests)
        return [
            schemas.CheckResponse(allowed=self.allowed, reason="fixture")
            for _ in requests
        ]


def item(effect="read", scopes=("read:x",)):
    return fleet_resource(
        "tool", "service/operation", required_scopes=scopes, effect=effect
    )


@pytest.mark.parametrize("mode", ["none", "local", "external"])
def test_profile_defaults(mode):
    assert policy_mode(mode) == ("none" if mode == "none" else "embedded")
    assert policy_mode(mode, "none") == "none"


async def test_off_still_requires_exact_scopes_and_principal():
    gate = PolicyGate("none")
    caller = Caller()
    assert await gate.visible([item(), item(scopes=("write:x",))], caller) == [
        True,
        False,
    ]
    assert await gate.loadable(
        [item()], Caller(effective_scopes=frozenset({"mcp:discover", "read:x"}))
    ) == [False]
    service_only = fleet_resource(
        "tool",
        "service/admin",
        required_scopes=("read:x",),
        principal_rule="service_only",
    )
    assert await gate.visible([service_only], caller) == [False]


async def test_enabled_denial_and_unavailable_are_distinct():
    pdp = PDP(False)
    gate = PolicyGate("embedded", pdp)
    assert await gate.visible([item()], Caller()) == [False]
    pdp.fail = True
    with pytest.raises(PolicyUnavailable):
        await gate.loadable([item()], Caller())


async def test_revision_cache_and_destructive_bypass():
    now = [0.0]
    changed: list[str] = []

    async def on_change(revision):
        changed.append(revision)

    pdp = PDP()
    gate = PolicyGate(
        "embedded",
        pdp,
        cache=DecisionCache(clock=lambda: now[0]),
        on_revision_change=on_change,
    )
    caller = Caller()
    assert await gate.visible([item()], caller) == [True]
    assert await gate.visible([item()], caller) == [True]
    assert len(pdp.requests) == 1
    now[0] = 31
    assert await gate.visible([item()], caller) == [True]
    assert len(pdp.requests) == 2
    assert await gate.decisions([item("destructive")], caller, "call") == [True]
    assert await gate.decisions([item("destructive")], caller, "call") == [True]
    assert len(pdp.requests) == 4
    assert await gate.visible([item()], Caller(policy_revision="rev-2")) == [True]
    assert changed == ["rev-2"]
    assert len(pdp.requests) == 5


async def test_bulk_bounded_and_attributes_have_no_token():
    pdp = PDP()
    gate = PolicyGate("embedded", pdp)
    selected = [
        fleet_resource("tool", f"server/tool-{i}", required_scopes=("read:x",))
        for i in range(205)
    ]
    assert all(await gate.visible(selected, Caller()))
    assert len(pdp.requests) == 205
    principal = pdp.requests[0].principal
    assert principal.attributes["tenant"] == "tenant-a"
    assert "token" not in principal.attributes


async def test_default_embedded_policy_denies_destructive_fleet_to_non_admin():
    gate = PolicyGate.from_config(SimpleNamespace(eunomia_policy_file=None), "local")
    assert await gate.visible([item("destructive"), item()], Caller()) == [False, True]
    admin = Caller(effective_scopes=Caller().effective_scopes | {"mcp:admin"})
    assert await gate.visible([item("destructive")], admin) == [True]


async def test_missing_pdp_fails_closed():
    gate = PolicyGate("embedded")
    with pytest.raises(PolicyUnavailable):
        await gate.visible([item()], Caller())


async def test_invalid_response_fails_closed():
    class InvalidPDP:
        async def bulk_check(self, requests):
            return [True for _ in requests]

    gate = PolicyGate("remote", InvalidPDP())
    with pytest.raises(PolicyUnavailable):
        await gate.visible([item()], Caller())


async def test_cache_is_partitioned_by_all_verified_authority_facts():
    class ScopePDP:
        def __init__(self):
            self.calls = 0

        async def bulk_check(self, requests):
            self.calls += 1
            return [
                schemas.CheckResponse(
                    allowed="extra:x" in request.principal.attributes["scopes"],
                    reason="fixture",
                )
                for request in requests
            ]

    pdp = ScopePDP()
    gate = PolicyGate("embedded", pdp)
    scoped = Caller(effective_scopes=Caller().effective_scopes | {"extra:x"})
    assert await gate.visible([item()], scoped) == [True]
    assert await gate.visible([item()], Caller()) == [False]
    assert pdp.calls == 2
