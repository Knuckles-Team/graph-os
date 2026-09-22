"""Focused safety tests for production backup/restore host mechanics."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from graph_os.deployment import production_ops


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (
            "tcp://engine.example.com:9876",
            {"tcp_addr": "engine.example.com:9876", "tls": False},
        ),
        (
            "tls://engine.example.com:9876",
            {"tcp_addr": "engine.example.com:9876", "tls": True},
        ),
        ("unix:///run/epistemic.sock", {"socket_path": "/run/epistemic.sock"}),
    ],
)
def test_coordinator_transport_uses_public_eg_client_fields(
    monkeypatch, endpoint: str, expected: dict[str, object]
) -> None:
    monkeypatch.setattr(
        production_ops,
        "AgentConfig",
        lambda: SimpleNamespace(graph_service_endpoints=[endpoint]),
    )

    assert production_ops._coordinator_transport() == expected


def test_coordinator_transport_rejects_implicit_scheme(monkeypatch) -> None:
    monkeypatch.setattr(
        production_ops,
        "AgentConfig",
        lambda: SimpleNamespace(graph_service_endpoints=["engine.example.com:9876"]),
    )

    with pytest.raises(production_ops.ProductionOperationError):
        production_ops._coordinator_transport()


def test_inside_rejects_targets_outside_mounted_root(tmp_path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    with pytest.raises(production_ops.ProductionOperationError):
        production_ops._inside(root, tmp_path / "outside")


def test_tree_digest_is_deterministic_and_counts_files(tmp_path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "b.txt").write_text("two", encoding="utf-8")
    (root / "a.txt").write_text("one", encoding="utf-8")

    first = production_ops._tree_digest(root)
    second = production_ops._tree_digest(root)

    assert first == second
    assert first[0].startswith("sha256:")
    assert first[1:] == (2, 6)


def test_recovery_manifest_requires_portable_admin_state(tmp_path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "admin-mutations.redb").write_bytes(b"state")
    (bundle / "MANIFEST.json").write_text(
        json.dumps(
            {
                "format_version": 3,
                "admin_mutations": {
                    "batches": 2,
                    "prepared": 1,
                    "encrypted_private_payloads": 1,
                },
                "xshard_prepares": 0,
                "xshard_decisions": 0,
            }
        ),
        encoding="utf-8",
    )

    assert production_ops._recovery_manifest(bundle) == {
        "admin_batches": 2,
        "prepared_parents": 1,
        "encrypted_recovery_plans": 1,
        "xshard_prepares": 0,
        "xshard_decisions": 0,
    }


def test_production_cli_failure_is_opaque(tmp_path, capsys, monkeypatch) -> None:
    for name in (
        "GRAPH_OS_BACKUP_PRINCIPAL",
        "GRAPH_OS_BACKUP_TENANT",
        "AUTH_JWT_AUDIENCE",
        "KG_POLICY_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = production_ops.main(
        ["backup", "--archive-root", str(tmp_path / "archive")]
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "backup"
    assert report["ok"] is False
    assert report["error_type"] == "ProductionOperationError"
    assert str(tmp_path) not in json.dumps(report)
