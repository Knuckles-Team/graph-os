"""The admin API reaches EG as the caller, with no broker authority."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.identity import admin_service
from graph_os.identity.admin_service import IdentityAdminService
from graph_os.identity.engine import IdentityCall, IdentityReply, IdentityUnavailable


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
