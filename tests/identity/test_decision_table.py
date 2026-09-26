"""The shared IDM-17 table and stable authorization claims across auth modes.

This exercises the real GraphOS broker and issuer with an engine port double.
The deterministic grant fixture checks the table locally; served EG acceptance
must run the same cases against the engine before accepting this row.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from functools import cache
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from joserfc.jwk import KeySet
from ldap3 import OFFLINE_SLAPD_2_4
from starlette.requests import Request

from graph_os.identity.broker import IdentityBroker
from graph_os.identity.engine import IdentityCall, IdentityReply
from graph_os.identity.idp_common import (
    SESSION_COOKIE,
    EngineLoginCompleter,
    ExternalAssertion,
    flatten_claims,
)
from graph_os.identity.issuer import IssuerSettings, LocalIssuer
from graph_os.identity.oidc import OidcSettings, verify_id_token

from .fakes import FakeIdentityPort
from .store_double import SecretsDouble, StoreDouble, _Session
from .test_ldap import BASE, GENERIC, _mock_directory, _person
from .test_oidc import CLIENT, ISSUER, REDIRECT, MockIdp

TABLE = Path(__file__).with_name("decision_table.yaml")


class LinkedStore(StoreDouble):
    """The two mock IdPs both link their subject to the existing principal."""

    def __init__(self, role: str = "reports-reader") -> None:
        super().__init__()
        self.role = role

    def _resolve_session(self, request: Mapping[str, Any]) -> IdentityReply:
        reply = super()._resolve_session(request)
        resolution = dict(reply.expect("resolution"))
        resolution["roles"] = [self.role]
        return IdentityReply("resolution", resolution)

    def _external_login(self, request: Mapping[str, Any]) -> IdentityReply:
        assert request["idp_id"] in {"mock-oidc", "mock-ldap"}
        expected_subject = {
            "mock-oidc": "bootstrap-subject",
            "mock-ldap": str(uuid.uuid5(uuid.NAMESPACE_DNS, "bootstrap")),
        }
        assert request["subject"] == expected_subject[request["idp_id"]]
        principal = "usr:bootstrap"
        self.sessions[str(request["session_token"])] = _Session(principal)
        return self._authenticated(principal)


class LinkedIdpPort(FakeIdentityPort):
    """Verified IdP assertions and the local broker share one store double."""

    def __init__(self, store: LinkedStore, idps: list[dict[str, Any]]) -> None:
        super().__init__(idps)
        self.store = store

    async def call(self, op: Mapping[str, Any]) -> Mapping[str, Any]:
        if (op["family"], op["op"]) != ("credential", "external_login"):
            return dict(await super().call(op))
        self.calls.append(json.loads(json.dumps(op)))
        reply = await self.store.broker(
            IdentityCall("credential", "external_login", op["request"])
        )
        return {"kind": reply.kind, "value": reply.value}


async def _complete_assertion(store: LinkedStore, assertion: ExternalAssertion) -> str:
    port = LinkedIdpPort(store, [])
    request = Request(
        {"type": "http", "path": "/auth/", "headers": [], "client": ("127.0.0.1", 443)}
    )
    response = await EngineLoginCompleter(port).complete(request, assertion)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert len(port.ops("credential", "external_login")) == 1
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie[SESSION_COOKIE].value


@cache
def _mock_idp() -> MockIdp:
    """Reuse the mock signing key across rows; each JWT remains fresh."""
    return MockIdp()


async def _mock_oidc_session(store: LinkedStore) -> str:
    """A mock IdP signs a token; GraphOS verifies it before engine linking."""
    idp = _mock_idp()
    nonce = "eh553-parity-nonce"
    now = int(time.time())
    token = idp.sign(
        {
            "iss": ISSUER,
            "aud": CLIENT,
            "sub": "bootstrap-subject",
            "exp": now + 300,
            "iat": now,
            "nonce": nonce,
        }
    )
    settings = OidcSettings(issuer=ISSUER, client_id=CLIENT, redirect_uri=REDIRECT)
    verified = verify_id_token(
        token, settings=settings, keys=KeySet([idp.key]), nonce=nonce, now=now
    )
    return await _complete_assertion(
        store,
        ExternalAssertion(
            idp_id="mock-oidc",
            subject=verified["sub"],
            claims=flatten_claims(verified, settings.claim_paths),
        ),
    )


async def _mock_ldap_session(store: LinkedStore) -> str:
    """The in-memory LDAP server proves the password by a real adapter bind."""
    directory = _mock_directory(
        GENERIC,
        OFFLINE_SLAPD_2_4,
        {f"uid=bootstrap,{BASE}": _person("bootstrap", "bootstrap-pw")},
    )
    entry = directory.find_user("bootstrap")
    assert entry is not None and entry.active
    assert directory.verify_password(entry.dn, "bootstrap-pw")
    return await _complete_assertion(
        store,
        ExternalAssertion(
            idp_id="mock-ldap",
            subject=entry.subject,
            claims=entry.claims(),
            username_hint=entry.username,
        ),
    )


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


def _engine_carrier(claims: dict[str, Any]) -> dict[str, Any]:
    """Use the served JWT-to-session projection, never hand-build EG claims."""
    from agent_utilities.security.request_identity import (
        actor_from_claims,
        mint_graph_session,
    )

    return mint_graph_session(actor_from_claims(claims)).engine_verified_context()


def _fixture_decision(
    table: dict[str, Any], claims: dict[str, Any], case: dict[str, Any]
) -> tuple[bool, str]:
    """Predict this fixture's exact graph grants, not EG's general RBAC policy."""
    if case["scope"] not in claims["scope"].split():
        return False, "SCOPE_DENIED"
    matching = [
        grant
        for grant in table["grants"]
        if grant["role"] == table["principal"]["role"]
        and grant["resource"] == {"Graph": case["resource"]}
        and grant["action"] == case["action"]
    ]
    assert len(matching) <= 1, case["id"]
    if not matching:
        return False, "NO_MATCHING_GRANT"
    if matching[0]["effect"] == "Deny":
        return False, "EXPLICIT_DENY"
    assert matching[0]["effect"] == "Allow", case["id"]
    return True, "STANDING_GRANT"


def test_decision_table_is_complete_and_unambiguous() -> None:
    table = _table()
    assert table["version"] == 1
    assert table["principal"]["id"] == "usr:bootstrap"
    assert len({case["id"] for case in table["cases"]}) == len(table["cases"])
    for case in table["cases"]:
        assert {"id", "scope", "action", "resource", "allow", "reason_code"} <= set(
            case
        )
        assert case["action"] in {"Read", "Write"}
        assert case["reason_code"] in {
            "STANDING_GRANT",
            "EXPLICIT_DENY",
            "NO_MATCHING_GRANT",
            "SCOPE_DENIED",
        }
        assert case["allow"] is (case["reason_code"] == "STANDING_GRANT")
        narrowed = case.get("narrow_scopes")
        assert bool(narrowed) == (case["reason_code"] == "SCOPE_DENIED")
        if narrowed:
            assert set(narrowed) < set(table["principal"]["scopes"])
            assert case["scope"] not in narrowed
    assert table["flip_with_data"]["assert_data_preserved"] is True


async def _claims_for_modes(
    scopes: frozenset[str] | None = None,
    on_mode: Callable[[str, dict[str, Any]], None] | None = None,
    role: str = "reports-reader",
) -> dict[str, dict[str, Any]]:
    table = _table()
    store = LinkedStore(role)
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
    none_claims = issuer.verify(
        broker.access_token(none_resolution, ("none",), scopes=scopes)
    )
    if on_mode is not None:
        on_mode("none", none_claims)

    await broker.transition(caller, "local")
    local = await broker.sign_in("bootstrap", bootstrap.password)
    assert local.session_token is not None
    local_resolution = await broker.resolve_session(local.session_token)
    assert local_resolution is not None
    local_claims = issuer.verify(
        broker.access_token(local_resolution, ("pwd",), scopes=scopes)
    )
    if on_mode is not None:
        on_mode("local", local_claims)

    await broker.transition(caller, "external")
    claims = {"none": none_claims, "local": local_claims}
    for idp, token in (
        ("mock-oidc", await _mock_oidc_session(store)),
        ("mock-ldap", await _mock_ldap_session(store)),
    ):
        resolution = await broker.resolve_session(token)
        assert resolution is not None
        claims[idp] = issuer.verify(
            broker.access_token(resolution, ("idp:" + idp,), scopes=scopes)
        )
        if on_mode is not None:
            on_mode(idp, claims[idp])
    return claims


@pytest.mark.parametrize("mode", ["none", "local", "mock-oidc", "mock-ldap"])
def test_one_principal_and_authorization_digest_in_every_mode(mode: str) -> None:
    claims = asyncio.run(_claims_for_modes())
    expected = _table()["principal"]
    assert claims[mode]["sub"] == expected["id"]
    assert claims[mode]["tenant_id"] == expected["tenant"]
    assert set(claims[mode]["scope"].split()) == set(expected["scopes"])
    assert claims[mode]["roles"] == [expected["role"]]
    assert claims[mode]["realm_access"]["roles"] == [expected["role"]]
    assert {_authorization_digest(value) for value in claims.values()} == {
        _authorization_digest(claims["none"])
    }


def test_verified_engine_carrier_is_identical_across_modes() -> None:
    claims_by_mode = asyncio.run(_claims_for_modes())
    carriers = {
        mode: _engine_carrier(claims) for mode, claims in claims_by_mode.items()
    }
    expected = carriers["none"]
    assert expected["principal"] == "usr:bootstrap"
    assert expected["agent_id"] == "usr:bootstrap"
    assert expected["tenant"] == "local"
    assert expected["audience"] == "graph-os-local"
    assert expected["policy_version"] == "identity-test-policy"
    assert expected["delegation"] == []
    assert expected["roles"] == ["reports-reader"]
    assert set(expected) - {"priority"} == {
        "principal",
        "tenant",
        "audience",
        "agent_id",
        "roles",
        "scopes",
        "policy_version",
        "delegation",
    }
    assert all(carrier == expected for carrier in carriers.values())

    narrowed_claims = asyncio.run(_claims_for_modes(frozenset({"identity:self"})))
    narrowed = {
        mode: _engine_carrier(claims) for mode, claims in narrowed_claims.items()
    }
    narrow_expected = narrowed["none"]
    assert narrow_expected["principal"] == expected["principal"]
    assert narrow_expected["tenant"] == expected["tenant"]
    assert "node:read" not in narrow_expected["scopes"]
    assert "kg:admin" not in narrow_expected["scopes"]
    assert all(carrier == narrow_expected for carrier in narrowed.values())


def test_opaque_role_name_cannot_expand_a_narrowed_scope() -> None:
    """An EG RBAC role ID may spell a scope, but is never a token grant."""
    claims_by_mode = asyncio.run(
        _claims_for_modes(frozenset({"identity:self"}), role="kg:admin")
    )
    for mode, claims in claims_by_mode.items():
        assert claims["roles"] == ["kg:admin"], mode
        assert claims["scope"] == "identity:self", mode
        carrier = _engine_carrier(claims)
        assert "kg:admin" not in carrier["scopes"], mode
        assert carrier["roles"] == ["kg:admin"], mode


@pytest.mark.parametrize("mode", ["none", "local", "mock-oidc", "mock-ldap"])
def test_decision_table_matches_grants_for_every_issued_mode(mode: str) -> None:
    table = _table()
    claims = asyncio.run(_claims_for_modes())[mode]
    narrowed = asyncio.run(_claims_for_modes(frozenset({"identity:self"})))[mode]
    for case in table["cases"]:
        case_claims = narrowed if case.get("narrow_scopes") else claims
        assert _fixture_decision(table, case_claims, case) == (
            case["allow"],
            case["reason_code"],
        ), (mode, case["id"])


def _engine_fixture() -> dict[str, str]:
    """A dedicated EG fixture with this table's principal, graphs and grants.

    The fixture is deliberately explicit: querying an arbitrary shared engine
    could compare against an unrelated or changing policy image.
    """
    names = {
        "socket_path": "EH553_EG_SOCKET",
        "auth_secret": "EH553_EG_AUTH_SECRET",
        "agent_id": "EH553_EG_CHECKER_ID",
        "audience": "EH553_EG_AUDIENCE",
        "tenant": "EH553_EG_TENANT",
        "policy_version": "EH553_EG_POLICY_VERSION",
    }
    present = {
        key: os.environ[name] for key, name in names.items() if os.environ.get(name)
    }
    if not present:
        pytest.skip("dedicated EH-553 EG decision fixture is not configured")
    if set(present) != set(names):
        pytest.fail("EH-553 EG decision fixture configuration is incomplete")
    return present


def test_engine_decision_table_is_identical_across_identity_modes() -> None:
    """Query EG's live ACL decision for the claims issued by every mode.

    The dedicated EG fixture must have `usr:bootstrap` registered with
    `reports-reader`, the table's two grants, and both named graphs. Its checker
    identity must hold `security:check` and read access to those graphs. The
    reports graph must already contain the public `eh553-parity-sentinel` node.
    Its continued visibility is checked at each live mode transition. EG's
    `CheckAccess` is a service-side recheck; a narrowed user token's missing
    scope is denied by the envelope before this API can return a decision.
    """
    fixture = _engine_fixture()
    from epistemic_graph.client import SyncEpistemicGraphClient

    table = _table()
    assert fixture["tenant"] == table["principal"]["tenant"]

    def connect(graph: str, context: dict[str, Any]) -> SyncEpistemicGraphClient:
        return SyncEpistemicGraphClient.connect(
            socket_path=fixture["socket_path"],
            auth_secret=fixture["auth_secret"],
            graph_name=graph,
            verified_context=context,
        )

    checker_context = {
        "principal": fixture["agent_id"],
        "tenant": fixture["tenant"],
        "audience": fixture["audience"],
        "agent_id": fixture["agent_id"],
        "roles": [],
        "scopes": ["security:check", "node:read"],
        "policy_version": fixture["policy_version"],
        "delegation": [],
    }
    sentinel = "eh553-parity-sentinel"
    reports_graph = table["flip_with_data"]["resource"]
    checker = connect(reports_graph, checker_context)
    try:
        assert checker.nodes.has(sentinel) is True
    finally:
        checker.close()

    def observe(mode: str, claims: dict[str, Any]) -> None:
        carrier = _engine_carrier(claims)
        assert carrier["principal"] == table["principal"]["id"], mode
        assert carrier["tenant"] == fixture["tenant"], mode
        assert carrier["audience"] == fixture["audience"], mode
        assert carrier["policy_version"] == fixture["policy_version"], mode
        reader = connect(reports_graph, carrier)
        try:
            assert reader.nodes.has(sentinel) is True, mode
            with pytest.raises(RuntimeError, match="ACCESS_DENIED"):
                reader.nodes.add(f"eh553-denied-{mode}", {"_visibility": "public"})
        finally:
            reader.close()
        private = connect("agent:private", carrier)
        try:
            with pytest.raises(RuntimeError, match="ACCESS_DENIED"):
                private.nodes.has(sentinel)
        finally:
            private.close()
        for case in table["cases"]:
            if case.get("narrow_scopes"):
                continue
            checker = connect(case["resource"], checker_context)
            try:
                decision = checker.consensus.check_access_decision(
                    carrier["agent_id"], case["action"].lower()
                )
            finally:
                checker.close()
            assert decision == {
                "agent_id": carrier["agent_id"],
                "graph": case["resource"],
                "access": case["action"].lower(),
                "allowed": case["allow"],
                "reason_code": case["reason_code"],
            }, (mode, case["id"])

    full_claims = asyncio.run(_claims_for_modes(on_mode=observe))
    assert len(full_claims) == 4

    scope_case = next(case for case in table["cases"] if case.get("narrow_scopes"))

    def observe_narrowed(mode: str, claims: dict[str, Any]) -> None:
        carrier = _engine_carrier(claims)
        assert scope_case["scope"] not in carrier["scopes"], mode
        assert "kg:admin" not in carrier["scopes"], mode
        reader = connect(reports_graph, carrier)
        try:
            with pytest.raises(RuntimeError, match="SCOPE_DENIED"):
                reader.nodes.has(sentinel)
        finally:
            reader.close()

    asyncio.run(
        _claims_for_modes(
            frozenset(scope_case["narrow_scopes"]), on_mode=observe_narrowed
        )
    )
