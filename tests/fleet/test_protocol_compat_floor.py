from __future__ import annotations

from pathlib import Path

from graph_os.fleet import protocol_compat


def test_graphos_declares_native_fastmcp_runtime_floor() -> None:
    requirement = protocol_compat._declared_runtime_floor("graph-os", "fastmcp")

    assert requirement is not None
    assert requirement.specifier.contains("4.0.0b1", prereleases=True)


def test_source_floor_is_read_from_graphos_manifest() -> None:
    requirement, manifest = protocol_compat._source_shadow_floor("fastmcp")

    assert requirement is not None
    assert requirement.specifier.contains("4.0.0b1", prereleases=True)
    expected = Path(protocol_compat.__file__).resolve().parents[2] / "pyproject.toml"
    assert manifest is not None and Path(manifest) == expected


def test_native_floor_check_uses_graphos_distribution() -> None:
    outcome = protocol_compat.check_mcp_sdk_floor()

    assert outcome["ok"] is True
    assert "fastmcp=" in outcome["detail"]
    assert "mcp=" in outcome["detail"]
