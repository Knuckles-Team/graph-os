"""The admin API reaches EG as the caller, with no broker authority."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.identity import admin_service
from graph_os.identity.admin_service import IdentityAdminService
from graph_os.identity.engine import (
    IdentityCall,
    IdentityRefused,
    IdentityReply,
    IdentityUnavailable,
)


class Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, IdentityCall]] = []
        self.answers: dict[tuple[str, str], IdentityReply] = {}

    async def broker(self, call: IdentityCall) -> IdentityReply:
        raise AssertionError(f"admin operation borrowed broker authority: {call}")

    async def as_caller(self, session: Any, call: IdentityCall) -> IdentityReply:
        self.calls.append((session, call))
        return self.answers[(call.family, call.op)]


@pytest.mark.asyncio
async def test_user_list_is_paged_under_caller_authority() -> None:
    engine = Engine()
    engine.answers["user", "list"] = IdentityReply(
        "users", [{"principal_id": "usr:2"}, {"principal_id": "usr:3"}]
    )
    caller = object()
    result = await IdentityAdminService(engine).execute(
        "identity.users.list", caller, {"after": "usr:1", "limit": 2}
    )
    assert result == {
        "items": [{"principal_id": "usr:2"}, {"principal_id": "usr:3"}],
        "next_cursor": "usr:3",
    }
    assert engine.calls == [
        (caller, IdentityCall("user", "list", {"after": "usr:1", "limit": 2}))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("op_id", "status"),
    [
        ("identity.users.disable", "disabled"),
        ("identity.users.enable", "active"),
        ("identity.users.deprovision", "deprovisioned"),
    ],
)
async def test_user_status_is_fixed_by_op_id(op_id: str, status: str) -> None:
    engine = Engine()
    engine.answers["user", "set_status"] = IdentityReply("done", {"changed": True})
    caller = object()
    await IdentityAdminService(engine).execute(
        op_id, caller, {"principal_id": "usr:2", "status": "active"}
    )
    assert engine.calls[0] == (
        caller,
        IdentityCall("user", "set_status", {"principal_id": "usr:2", "status": status}),
    )


@pytest.mark.asyncio
async def test_unknown_op_and_missing_caller_fail_closed() -> None:
    service = IdentityAdminService(Engine())
    with pytest.raises(IdentityUnavailable):
        await service.execute("identity.users.delete", object(), {})
    with pytest.raises(IdentityUnavailable):
        await service.execute("identity.users.list", None, {})


@pytest.mark.asyncio
async def test_mapping_preview_uses_live_directory_without_mutation() -> None:
    engine = Engine()
    engine.answers["idp", "list"] = IdentityReply(
        "idps",
        [
            {
                "idp_id": "corp",
                "rules": [
                    {
                        "rule_id": "r1",
                        "claim_path": "team",
                        "match_kind": "equals",
                        "value": "ops",
                        "target": "group:operators",
                    }
                ],
            }
        ],
    )
    engine.answers["access", "list_roles"] = IdentityReply(
        "roles", [{"role_id": "reader", "scopes": ["kg:read"]}]
    )
    engine.answers["access", "list_groups"] = IdentityReply(
        "groups", [{"group_id": "operators", "roles": ["reader"]}]
    )
    result = await IdentityAdminService(engine).mapping_dry_run(
        object(), "corp", {"team": ["ops"]}
    )
    assert result["groups"] == ["operators"]
    assert result["roles"] == ["reader"]
    assert result["scopes"] == ["kg:read"]
    assert len(engine.calls) == 3


@pytest.mark.asyncio
async def test_composite_entry_uses_verified_caller_session(monkeypatch: Any) -> None:
    engine = Engine()
    engine.answers["user", "get"] = IdentityReply("user", {"principal_id": "usr:2"})
    monkeypatch.setattr(admin_service, "EngineIdentityPort", lambda *_args: engine)
    caller_session = object()
    context = SimpleNamespace(
        client=object(), caller=SimpleNamespace(session=caller_session)
    )
    result = await admin_service.execute_identity_op(
        context, {"principal_id": "usr:2"}, SimpleNamespace(id="identity.users.get")
    )
    assert result == {"principal_id": "usr:2"}
    assert engine.calls == [
        (caller_session, IdentityCall("user", "get", {"id": "usr:2"}))
    ]


@pytest.mark.asyncio
async def test_search_and_api_key_cursor_use_eg_contract() -> None:
    engine = Engine()
    engine.answers["user", "search"] = IdentityReply(
        "users", [{"principal_id": "usr:2"}]
    )
    engine.answers["token", "list_api_keys"] = IdentityReply(
        "api_keys", [{"key_id": "key:9", "principal_id": "usr:2"}]
    )
    service = IdentityAdminService(engine)
    users = await service.execute(
        "identity.users.search", object(), {"query": "alice", "limit": 1}
    )
    keys = await service.execute(
        "identity.api_keys.list", object(), {"principal_id": "usr:2", "limit": 1}
    )
    assert users["next_cursor"] == "usr:2"
    assert keys["next_cursor"] == "key:9"
    assert engine.calls[0][1] == IdentityCall(
        "user", "search", {"query": "alice", "limit": 1}
    )
    assert engine.calls[1][1] == IdentityCall(
        "token", "list_api_keys", {"principal_id": "usr:2", "limit": 1}
    )


@pytest.mark.asyncio
async def test_mode_transition_uses_broker_for_issuer_rotation() -> None:
    class Broker:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, str, str | None, str | None]] = []

        async def transition(
            self, caller: Any, to: str, *, ack: str | None, local_fallback: str | None
        ) -> dict[str, Any]:
            self.calls.append((caller, to, ack, local_fallback))
            return {"mode": to, "epoch": 2}

    broker = Broker()
    caller_session = object()
    context = SimpleNamespace(
        services={"identity": broker},
        caller=SimpleNamespace(session=caller_session),
    )
    result = await admin_service.execute_identity_op(
        context,
        {"to": "local", "local_fallback": "break_glass"},
        SimpleNamespace(id="identity.mode.transition"),
    )
    assert result["mode"] == "local"
    assert broker.calls == [(caller_session, "local", None, "break_glass")]


@pytest.mark.asyncio
async def test_self_password_change_uses_caller_context() -> None:
    engine = Engine()
    engine.answers["credential", "change_password"] = IdentityReply(
        "done", {"changed": True}
    )
    caller = object()
    result = await IdentityAdminService(engine).execute(
        "identity.self.password.change",
        caller,
        {"current": "old-secret", "new": "new-secret"},
    )
    assert result == {"changed": True}
    assert engine.calls == [
        (
            caller,
            IdentityCall(
                "credential",
                "change_password",
                {"current": "old-secret", "new": "new-secret"},
            ),
        )
    ]


@pytest.mark.asyncio
async def test_audit_export_verify_and_service_account_list() -> None:
    engine = Engine()
    engine.answers["config", "export_audit"] = IdentityReply(
        "audit", [{"seq": 7, "chain": "digest"}]
    )
    engine.answers["config", "verify_audit"] = IdentityReply(
        "audit_verification", {"valid": True, "first_broken_seq": None}
    )
    engine.answers["user", "list_service_accounts"] = IdentityReply(
        "users", [{"principal_id": "svc:one", "kind": "service"}]
    )
    service = IdentityAdminService(engine)
    caller = object()
    exported = await service.execute("identity.audit.export", caller, {"limit": 1})
    verified = await service.execute("identity.audit.verify", caller, {})
    accounts = await service.execute(
        "identity.service_accounts.list", caller, {"limit": 1}
    )
    assert exported["next_cursor"] == "7"
    assert verified["valid"] is True
    assert accounts["items"][0]["kind"] == "service"
    assert all(session is caller for session, _ in engine.calls)


@pytest.mark.asyncio
async def test_service_account_create_fixes_kind_server_side() -> None:
    engine = Engine()
    engine.answers["user", "create"] = IdentityReply(
        "principal", {"principal_id": "svc:two"}
    )
    caller = object()
    result = await IdentityAdminService(engine).execute(
        "identity.service_accounts.create",
        caller,
        {"username": "sync-bot", "kind": "human"},
    )
    assert result == {"principal_id": "svc:two"}
    assert engine.calls == [
        (
            caller,
            IdentityCall("user", "create", {"username": "sync-bot", "kind": "service"}),
        )
    ]


@pytest.mark.asyncio
async def test_policy_update_carries_session_bounds_as_caller() -> None:
    engine = Engine()
    engine.answers["config", "update_policy"] = IdentityReply(
        "config", {"epoch": 3, "idle_ms": 600_000}
    )
    caller = object()
    result = await IdentityAdminService(engine).execute(
        "identity.policy.set", caller, {"expected_epoch": 2, "idle_ms": 600_000}
    )
    assert result["idle_ms"] == 600_000
    assert engine.calls == [
        (
            caller,
            IdentityCall(
                "config", "update_policy", {"expected_epoch": 2, "idle_ms": 600_000}
            ),
        )
    ]


@pytest.mark.asyncio
async def test_scim_client_binding_is_typed_and_caller_bound() -> None:
    engine = Engine()
    engine.answers["idp", "upsert_scim_client"] = IdentityReply(
        "done", {"changed": True}
    )
    engine.answers["idp", "get_scim_client"] = IdentityReply(
        "scim_client",
        {"idp_id": "corp-scim", "principal_id": "svc:provision", "enabled": True},
    )
    caller = object()
    service = IdentityAdminService(engine)
    await service.execute(
        "identity.scim_clients.upsert",
        caller,
        {"idp_id": "corp-scim", "principal_id": "svc:provision"},
    )
    found = await service.execute(
        "identity.scim_clients.get", caller, {"idp_id": "corp-scim"}
    )
    assert found["principal_id"] == "svc:provision"
    assert engine.calls == [
        (
            caller,
            IdentityCall(
                "idp",
                "upsert_scim_client",
                {"idp_id": "corp-scim", "principal_id": "svc:provision"},
            ),
        ),
        (caller, IdentityCall("idp", "get_scim_client", {"id": "corp-scim"})),
    ]


@pytest.mark.asyncio
async def test_explicit_issuer_rotation_records_kid_as_caller() -> None:
    events: list[str] = []

    class Issuer:
        def rotate(self, retirement: str) -> str:
            assert retirement == "overlap"
            events.append("signer")
            return "kid-2"

        def ring(self) -> Any:
            return SimpleNamespace(kid="kid-2")

    class Broker:
        issuer = Issuer()

        async def config(self, *, fresh: bool) -> dict[str, Any]:
            assert fresh is True
            events.append("config")
            return {"epoch": 1}

        def forget_config(self) -> None:
            events.append("invalidate")

    class RotationEngine(Engine):
        async def as_caller(self, session: Any, call: IdentityCall) -> IdentityReply:
            events.append("engine")
            return await super().as_caller(session, call)

    engine = RotationEngine()
    engine.answers["config", "rotate_issuer"] = IdentityReply(
        "config", {"epoch": 2, "issuer_kid_current": "kid-2"}
    )
    broker = Broker()
    broker.engine = engine
    session = object()
    context = SimpleNamespace(
        services={"identity": broker}, caller=SimpleNamespace(session=session)
    )
    result = await admin_service.execute_identity_op(
        context, {}, SimpleNamespace(id="identity.issuer.rotate")
    )
    assert result["issuer_kid_current"] == "kid-2"
    assert events == ["config", "signer", "engine", "invalidate"]
    assert engine.calls == [
        (
            session,
            IdentityCall(
                "config", "rotate_issuer", {"expected_epoch": 1, "issuer_kid": "kid-2"}
            ),
        )
    ]


@pytest.mark.asyncio
async def test_issuer_rotation_refusal_does_not_report_success() -> None:
    invalidated: list[bool] = []

    class Broker:
        issuer = SimpleNamespace(rotate=lambda _retirement: "kid-2")

        async def config(self, *, fresh: bool) -> dict[str, int]:
            return {"epoch": 1}

        def forget_config(self) -> None:
            invalidated.append(True)

        class engine:
            @staticmethod
            async def as_caller(_session: Any, _call: IdentityCall) -> IdentityReply:
                raise IdentityRefused("IDENTITY_EPOCH_CONFLICT")

    context = SimpleNamespace(
        services={"identity": Broker()}, caller=SimpleNamespace(session=object())
    )
    with pytest.raises(IdentityRefused, match="IDENTITY_EPOCH_CONFLICT"):
        await admin_service.execute_identity_op(
            context, {}, SimpleNamespace(id="identity.issuer.rotate")
        )
    assert invalidated == [True]


@pytest.mark.asyncio
async def test_issuer_rotation_refuses_newer_signer_race() -> None:
    class Broker:
        issuer = SimpleNamespace(
            rotate=lambda _retirement: "kid-2",
            ring=lambda: SimpleNamespace(kid="kid-3"),
        )

        async def config(self, *, fresh: bool) -> dict[str, int]:
            return {"epoch": 1}

        def forget_config(self) -> None:
            pass

        class engine:
            @staticmethod
            async def as_caller(_session: Any, _call: IdentityCall) -> IdentityReply:
                return IdentityReply("config", {"epoch": 2})

    context = SimpleNamespace(
        services={"identity": Broker()}, caller=SimpleNamespace(session=object())
    )
    with pytest.raises(IdentityUnavailable, match="rotated concurrently"):
        await admin_service.execute_identity_op(
            context, {}, SimpleNamespace(id="identity.issuer.rotate")
        )


@pytest.mark.asyncio
async def test_admin_reset_issues_token_as_verified_caller(monkeypatch: Any) -> None:
    monkeypatch.setattr(admin_service, "new_token", lambda: "one-time-token")
    engine = Engine()
    engine.answers["token", "issue_admin_reset"] = IdentityReply(
        "done", {"changed": True}
    )
    service = IdentityAdminService(engine)
    with pytest.raises(IdentityUnavailable, match="verified caller"):
        await service.admin_reset(None, "usr:2")
    assert engine.calls == []
    caller = object()
    result = await service.admin_reset(caller, "usr:2")
    assert result == {"reset_token": "one-time-token", "expires_in_minutes": 30}
    assert engine.calls == [
        (
            caller,
            IdentityCall(
                "token",
                "issue_admin_reset",
                {
                    "principal_id": "usr:2",
                    "token": "one-time-token",
                    "ttl_ms": 1_800_000,
                },
            ),
        )
    ]


@pytest.mark.asyncio
async def test_self_reads_derive_target_in_engine() -> None:
    engine = Engine()
    engine.answers["user", "resolve_self"] = IdentityReply(
        "resolution", {"principal_id": "usr:alice", "scopes": ["identity:self"]}
    )
    engine.answers["session", "list_own"] = IdentityReply(
        "sessions", [{"handle": "opaque-handle"}]
    )
    engine.answers["token", "list_own_api_keys"] = IdentityReply(
        "api_keys", [{"key_id": "key-2"}]
    )
    engine.answers["mfa", "status"] = IdentityReply(
        "mfa_status",
        {
            "totp_enrolled": True,
            "webauthn_credentials": 1,
            "recovery_codes_left": 5,
        },
    )
    service = IdentityAdminService(engine)
    caller = object()
    assert (await service.execute("identity.self.profile", caller, {}))[
        "principal_id"
    ] == "usr:alice"
    assert await service.execute("identity.self.sessions.list", caller, {}) == {
        "items": [{"handle": "opaque-handle"}],
        "next_cursor": None,
    }
    assert await service.execute(
        "identity.self.api_keys.list", caller, {"limit": 1}
    ) == {
        "items": [{"key_id": "key-2"}],
        "next_cursor": "key-2",
    }
    assert (await service.execute("identity.self.mfa.status", caller, {}))[
        "totp_enrolled"
    ] is True
    assert engine.calls == [
        (caller, IdentityCall("user", "resolve_self")),
        (caller, IdentityCall("session", "list_own")),
        (caller, IdentityCall("token", "list_own_api_keys", {"limit": 1})),
        (caller, IdentityCall("mfa", "status")),
    ]
