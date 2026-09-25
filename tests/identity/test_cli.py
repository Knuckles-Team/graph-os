"""``graph-os-identity`` operator commands (IDM-16) over the store double."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest

from graph_os.identity import keycloak_cli
from graph_os.identity.cli import CliIo, run
from graph_os.identity.keycloak import KeycloakUser
from graph_os.identity.setup_gate import seed_first_boot

from .gate_harness import serve
from .store_double import StoreDouble, StoredUser

ADMIN_PASSWORD = "correct horse battery"


class Terminal:
    """Scripted prompts and captured JSON lines."""

    def __init__(self, answers: list[str]) -> None:
        self._answers: Iterator[str] = iter(answers)
        self.lines: list[dict[str, Any]] = []

    def io(self) -> CliIo:
        return CliIo(prompt=lambda _: next(self._answers), write=self._write)

    def _write(self, line: str) -> None:
        self.lines.append(json.loads(line))


def _run(
    runtime: Any, argv: list[str], answers: list[str] | None = None
) -> tuple[int, Terminal]:
    terminal = Terminal(answers or [])
    return asyncio.run(run(runtime, argv, terminal.io())), terminal


def _demo() -> Any:
    served = serve(StoreDouble())
    asyncio.run(seed_first_boot(served.runtime.admission, "none"))
    return served


def _local() -> Any:
    served = serve(StoreDouble(), profile="single-node-prod")
    asyncio.run(served.runtime.broker.initialize("local", "root", ADMIN_PASSWORD))
    return served


def test_claim_names_the_bootstrap_administrator_and_leaves_none() -> None:
    served = _demo()
    code, terminal = _run(
        served.runtime, ["claim", "--username", "alice"], ["pw-1", "pw-1"]
    )
    assert code == 0
    assert terminal.lines == [
        {"claimed": "usr:bootstrap", "mode": "local", "username": "alice"}
    ]
    calls = [(family, op) for _, family, op in served.store.caller_calls]
    assert calls == [
        ("user", "update"),
        ("credential", "set_password"),
        ("config", "transition"),
    ]
    assert all(actor == "usr:bootstrap" for actor, _, _ in served.store.caller_calls)


def test_claim_refuses_mismatched_passwords() -> None:
    code, terminal = _run(_demo().runtime, ["claim", "--username", "a"], ["one", "two"])
    assert code == 2 and "differ" in terminal.lines[0]["error"]


def test_claim_only_leaves_none() -> None:
    code, terminal = _run(_local().runtime, ["claim", "--username", "a"])
    assert code == 2 and "not in none mode" in terminal.lines[0]["error"]


def test_transition_signs_the_administrator_in_and_rotates_the_key() -> None:
    served = _local()
    kid = served.runtime.broker.issuer.ring().kid
    code, terminal = _run(
        served.runtime,
        ["transition", "--to", "external", "--as", "root"],
        [ADMIN_PASSWORD],
    )
    assert code == 0 and terminal.lines == [{"epoch": 2, "mode": "external"}]
    assert served.runtime.broker.issuer.ring().kid != kid


def test_a_wrong_password_acts_on_nothing() -> None:
    served = _local()
    code, terminal = _run(
        served.runtime, ["transition", "--to", "none", "--as", "root"], ["x"]
    )
    assert code == 2 and "sign-in refused" in terminal.lines[0]["error"]
    assert served.store.config and served.store.config["mode"] == "local"


def test_outside_none_mode_an_administrator_is_required() -> None:
    code, terminal = _run(_local().runtime, ["reset-admin", "--principal", "usr:x"])
    assert code == 2 and "--as" in terminal.lines[0]["error"]


def test_reset_admin_prints_a_single_use_token() -> None:
    served = _local()
    bob = served.store.add_user("bob", "old")
    argv = ["reset-admin", "--principal", bob.principal_id, "--as", "root"]
    code, terminal = _run(served.runtime, argv, [ADMIN_PASSWORD])
    token = terminal.lines[0]["reset_token"]
    assert code == 0 and served.store.one_time[token] == (
        "admin_reset",
        bob.principal_id,
    )


def test_link_claim_issues_a_ten_minute_code_for_the_operator() -> None:
    served = _demo()
    code, terminal = _run(served.runtime, ["link-claim"])
    line = terminal.lines[0]
    assert code == 0 and line["expires_in_minutes"] == 10
    assert served.store.one_time[line["link_code"]] == ("link_claim", "usr:bootstrap")


@pytest.mark.parametrize(("flag", "published"), [([], 2), (["--revoke"], 1)])
def test_rotate(flag: list[str], published: int) -> None:
    served = _local()
    served.runtime.broker.issuer.ring()
    code, terminal = _run(served.runtime, ["rotate", *flag])
    assert (
        code == 0
        and terminal.lines[0]["kid"] == served.runtime.broker.issuer.ring().kid
    )
    assert len(served.runtime.broker.issuer.jwks()["keys"]) == published


def test_second_factor_is_prompted_for_an_enrolled_administrator() -> None:
    served = _local()
    root: StoredUser = next(
        u for u in served.store.users.values() if u.username == "root"
    )
    root.totp, root.totp_confirmed = "SECRET", True
    code, _ = _run(
        served.runtime,
        ["transition", "--to", "external", "--as", "root"],
        [ADMIN_PASSWORD, "123456"],
    )
    assert code == 0


SCOPES = {"kg:read": "user", "kg:admin": "admin", "capacity:read": "service-only"}


def test_keycloak_preset_dry_run_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keycloak_cli, "scope_classes", lambda: SCOPES)
    served = _local()
    argv = [
        "keycloak-preset",
        "--realm-url",
        "https://kc.test/realms/homelab",
        "--client-id",
        "graph-os",
        "--redirect-uri",
        "https://go.test/auth/oidc/callback",
        "--realm-role",
        "kg:read",
        "--realm-role",
        "capacity:read",
        "--group",
        "operators",
    ]
    code, terminal = _run(served.runtime, argv)
    line = terminal.lines[0]
    assert code == 0 and line["applied"] is False and line["sent"] == 0
    assert [role["role_id"] for role in line["roles"]] == ["kg:read"]
    assert "role:capacity:read" in line["skipped"]
    assert served.store.caller_calls == []


def test_link_migration_dry_run_reports_zero_ownership_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [
        KeycloakUser("kc-sub-1", "alice"),
        KeycloakUser("svc", "service-account-x", service_account=True),
    ]
    monkeypatch.setattr(keycloak_cli, "resolve_secret", lambda ref: "admin-token")
    monkeypatch.setattr(keycloak_cli, "realm_users", lambda url, realm, token: users)
    served = _local()
    argv = [
        "link-migration",
        "--keycloak-url",
        "https://kc.test",
        "--realm",
        "homelab",
        "--admin-token-ref",
        "vault://kc/admin",
        "--as",
        "root",
    ]
    code, terminal = _run(served.runtime, argv, [ADMIN_PASSWORD])
    line = terminal.lines[0]
    assert code == 0 and line["ownership_changes"] == 0
    assert line["create"] == 1 and line["link"] == 1 and line["applied"] is None
