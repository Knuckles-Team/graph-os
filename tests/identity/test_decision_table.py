"""The shared IDM-17 table and stable authorization claims across auth modes.

This exercises the real GraphOS broker and issuer with an engine port double.
The EG decision half of IDM-17 awaits the engine's reason-code contract;
the T7 gate must run the same table against EG before accepting this row.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.identity.broker import IdentityBroker
from graph_os.identity.engine import IdentityCall, IdentityReply
from graph_os.identity.issuer import IssuerSettings, LocalIssuer

from .store_double import SecretsDouble, StoreDouble, _Session

TABLE = Path(__file__).with_name("decision_table.yaml")


class LinkedStore(StoreDouble):
    """The two mock IdPs both link their subject to the existing principal."""

    def _external_login(self, request: dict[str, Any]) -> IdentityReply:
        assert request["idp_id"] in {"mock-oidc", "mock-ldap"}
        assert request["subject"] == "bootstrap-subject"
        principal = "usr:bootstrap"
        self.sessions[str(request["session_token"])] = _Session(principal)
        return self._authenticated(principal)


def _table() -> dict[str, Any]:
    # JSON is a YAML 1.2 subset. Keeping this fixture JSON-shaped lets both
    # repos consume it without adding a test-only YAML dependency.
    return json.loads(TABLE.read_text())


def _authorization_digest(claims: dict[str, Any]) -> str:
    """Only authorization facts belong to the cross-mode digest.

    ``jti``, times and ``amr`` necessarily differ by issuance and credential.
    The EG envelope must receive this same principal, tenant and scope set.
    """
    facts = {
        "sub": claims["sub"],
        "tenant_id": claims["tenant_id"],
        "scope": sorted(claims["scope"].split()),
        "roles": sorted(claims["roles"]),
        "realm_roles": sorted(claims["realm_access"]["roles"]),
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def test_decision_table_is_complete_and_unambiguous() -> None:
    table = _table()
    assert table["version"] == 1
    assert table["principal"]["id"] == "usr:bootstrap"
    assert len({case["id"] for case in table["cases"]}) == len(table["cases"])
    for case in table["cases"]:
        assert {"id", "scope", "action", "resource", "allow", "reason_code"} <= set(case)
        assert case["action"] in {"Read", "Write"}
        assert case["reason_code"] in {"Standing", "Denied", "SCOPE_DENIED"}
    assert table["flip_with_data"]["assert_data_preserved"] is True


async def _claims_for_modes() -> dict[str, dict[str, Any]]:
    table = _table()
    store = LinkedStore()
    issuer = LocalIssuer(
        SecretsDouble(),
        IssuerSettings(issuer="https://graph-os.test", audience="eg", tenant="local"),
        clock=lambda: 1_900_000_000.0,
    )
    broker = IdentityBroker(store, issuer, clock=lambda: 1_900_000_000.0)
    await broker.initialize("none")
    bootstrap = store.users["usr:bootstrap"]
    bootstrap.scopes = frozenset(table["principal"]["scopes"])
    bootstrap.password = "a long bootstrap credential"
    caller = SimpleNamespace(actor=SimpleNamespace(actor_id="usr:bootstrap"))

    none_session = await broker.bootstrap_session()
    none_resolution = await broker.resolve_session(none_session)
    assert none_resolution is not None
    none_claims = issuer.verify(broker.access_token(none_resolution, ("none",)))

    await broker.transition(caller, "local")
    local = await broker.sign_in("bootstrap", bootstrap.password)
    assert local.session_token is not None
    local_resolution = await broker.resolve_session(local.session_token)
    assert local_resolution is not None
    local_claims = issuer.verify(broker.access_token(local_resolution, ("pwd",)))

    await broker.transition(caller, "external")
    # Mock OIDC and LDAP verification have their own protocol tests. Here
    # each verified assertion enters the same engine link and token path.
    claims = {"none": none_claims, "local": local_claims}
    for idp in ("mock-oidc", "mock-ldap"):
        token = f"session-{idp}"
        await store.broker(
            IdentityCall(
                "credential",
                "external_login",
                {
                    "idp_id": idp,
                    "subject": "bootstrap-subject",
                    "session_token": token,
                },
            )
        )
        resolution = await broker.resolve_session(token)
        assert resolution is not None
        claims[idp] = issuer.verify(broker.access_token(resolution, ("idp:" + idp,)))
    return claims


@pytest.mark.parametrize("mode", ["none", "local", "mock-oidc", "mock-ldap"])
def test_one_principal_and_authorization_digest_in_every_mode(mode: str) -> None:
    claims = asyncio.run(_claims_for_modes())
    expected = _table()["principal"]
    assert claims[mode]["sub"] == expected["id"]
    assert claims[mode]["tenant_id"] == expected["tenant"]
    assert set(claims[mode]["scope"].split()) == set(expected["scopes"])
    assert {_authorization_digest(value) for value in claims.values()} == {
        _authorization_digest(claims["none"])
    }
