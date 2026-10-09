"""GRAPHOS-HOST-R024.1: the production invoke-services composition, against
EG client fixtures only -- this module never opens a live connection."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os import epistemic
from graph_os.api.invoke import bootstrap
from graph_os.api.invoke.steps import VerifiedCaller


@dataclass
class Actor:
    """A fake structurally satisfying ``graph_os.epistemic.VerifiedActor``."""

    actor_id: str = "service:graph-os"
    tenant_id: str = "tenant:a"
    roles: Iterable[str] = ("graph-client",)
    authenticated: bool = True
    current: bool = True

    def ensure_credential_current(self) -> None:
        if not self.current:
            raise PermissionError("expired")


@dataclass
class FakeSession:
    actor: Actor
    tenant: str

    def engine_verified_context(self) -> dict[str, Any]:
        return {
            "principal": self.actor.actor_id,
            "tenant": self.tenant,
            "audience": "epistemic-graph",
            "agent_id": self.actor.actor_id,
            "roles": list(self.actor.roles),
            "scopes": ["example:ask"],
            "delegation": [],
            "policy_version": "policy:1",
        }


def _caller(session: Any, **changes: Any) -> VerifiedCaller:
    fields = dict(
        principal="service:graph-os",
        tenant="tenant:a",
        effective_scopes=frozenset({"example:ask"}),
        engine_claims={},
        principal_kind="service",
        authenticated=True,
        delegated=False,
        credential_kind="session",
        policy_revision="policy:0",
        request_id="request:one",
        session=session,
    )
    fields.update(changes)
    return VerifiedCaller(**fields)


def test_verify_current_from_session_refreshes_claims() -> None:
    session = FakeSession(actor=Actor(), tenant="tenant:a")
    refreshed = asyncio.run(bootstrap.verify_current_from_session(_caller(session)))
    assert refreshed.engine_claims["policy_version"] == "policy:1"
    assert refreshed.principal == "service:graph-os"


def test_verify_current_from_session_rejects_actor_mismatch() -> None:
    session = FakeSession(actor=Actor(actor_id="other"), tenant="tenant:a")
    with pytest.raises(bootstrap.SessionRequiredError):
        asyncio.run(bootstrap.verify_current_from_session(_caller(session)))


def test_verify_current_from_session_requires_a_session() -> None:
    with pytest.raises(bootstrap.SessionRequiredError):
        asyncio.run(bootstrap.verify_current_from_session(_caller(None)))


class FakeClient:
    """A fake satisfying ``EpistemicClient.use_verified_context``."""

    def __init__(self, graph: str, context: Mapping[str, Any]) -> None:
        self.graph = graph
        self.context = context

    @contextlib.contextmanager
    def use_verified_context(self, context: Mapping[str, Any]):
        previous = self.context
        self.context = context
        try:
            yield self
        finally:
            self.context = previous

    async def close(self) -> None:
        return None


def test_build_client_factory_reuses_the_pooled_client() -> None:
    created: list[str] = []

    async def connect(**kwargs: Any) -> FakeClient:
        created.append(kwargs["graph_name"])
        return FakeClient(kwargs["graph_name"], kwargs["verified_context"])

    pool = epistemic.EpistemicClientPool(connect, auth_secret="test-secret")
    factory = bootstrap.build_client_factory(
        pool,
        graph="graphos:ops",
        connect_context=lambda tenant: epistemic.ClientContext.from_actor(
            Actor(),
            tenant=tenant,
            audience="epistemic-graph",
            scopes=("graph:read",),
            policy_version="policy:1",
        ),
    )
    first = asyncio.run(factory("tenant:a"))
    second = asyncio.run(factory("tenant:a"))
    assert first is second
    assert created == ["graphos:ops"]


def test_build_service_claims_mints_exact_requested_scopes() -> None:
    claims_fn = bootstrap.build_service_claims(
        lambda: Actor(), audience="epistemic-graph", policy_version="policy:1"
    )
    claims = asyncio.run(claims_fn("tenant:a", frozenset({"example:execute"})))
    assert claims["scopes"] == ["example:execute"]
    assert claims["principal"] == "service:graph-os"
    assert claims["tenant"] == "tenant:a"


def test_build_service_claims_refuses_a_mismatched_tenant() -> None:
    claims_fn = bootstrap.build_service_claims(
        lambda: Actor(), audience="epistemic-graph", policy_version="policy:1"
    )
    with pytest.raises(epistemic.AuthorityError):
        asyncio.run(claims_fn("tenant:other", frozenset({"example:execute"})))


def test_deny_subject_access_denies() -> None:
    allowed = asyncio.run(bootstrap.deny_subject_access(object(), "subject:one"))
    assert allowed is False


def test_unbridged_eg_dispatch_fails_closed() -> None:
    binding = SimpleNamespace(service="eg.example.method")
    with pytest.raises(Exception) as excinfo:
        asyncio.run(bootstrap.unbridged_eg_dispatch(binding, {}, None))
    # The pinned test environment's installed epistemic_graph wheel predates
    # EngineResponseError (a drift found by this PR, tracked separately, not
    # fixed here); either exception proves this fails closed, not silently.
    assert excinfo.type.__name__ in {"EngineResponseError", "ImportError"}
