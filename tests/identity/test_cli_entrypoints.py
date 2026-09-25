"""``setup-config identity`` is ``graph-os-identity`` (one CLI, two doors)."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.deployment import cli as deployment_cli
from graph_os.identity import cli as identity_cli


def test_setup_config_forwards_every_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(identity_cli, "main", lambda argv: seen.append(argv) or 0)
    assert deployment_cli.main(["identity", "rotate", "--revoke"]) == 0
    assert seen == [["rotate", "--revoke"]]


def test_the_parser_knows_every_operator_command() -> None:
    parser = identity_cli.build_parser()
    for argv in (
        ["claim", "--username", "a"],
        ["transition", "--to", "local", "--as", "root"],
        ["reset-admin", "--principal", "usr:x"],
        ["link-claim"],
        ["rotate"],
        [
            "keycloak-preset",
            "--realm-url",
            "u",
            "--client-id",
            "c",
            "--redirect-uri",
            "r",
        ],
        [
            "link-migration",
            "--keycloak-url",
            "u",
            "--realm",
            "r",
            "--admin-token-ref",
            "x",
        ],
    ):
        assert parser.parse_args(argv).command == argv[0]
    with pytest.raises(SystemExit):
        parser.parse_args(["transition", "--to", "open"])
