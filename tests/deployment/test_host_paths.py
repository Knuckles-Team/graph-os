"""GraphOS host path compatibility with the deployed XDG tree."""

from __future__ import annotations

from pathlib import Path

from graph_os.deployment import paths


def test_host_paths_preserve_existing_directory_overrides(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "operator-config"
    data = tmp_path / "runtime-data"
    monkeypatch.setenv("AGENT_UTILITIES_CONFIG_DIR", str(config))
    monkeypatch.setenv("AGENT_UTILITIES_DATA_DIR", str(data))

    assert paths.config_dir() == config
    assert paths.data_dir() == data
    assert paths.mcp_config_path() == config / "mcp_config.json"


def test_host_paths_preserve_existing_platform_defaults(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENT_UTILITIES_CONFIG_DIR", raising=False)
    monkeypatch.delenv("AGENT_UTILITIES_DATA_DIR", raising=False)
    observed: list[tuple[str, str]] = []

    def user_config_path(name: str, author: str) -> Path:
        observed.append((name, author))
        return tmp_path / "config"

    def user_data_path(name: str, author: str) -> Path:
        observed.append((name, author))
        return tmp_path / "data"

    monkeypatch.setattr(paths.platformdirs, "user_config_path", user_config_path)
    monkeypatch.setattr(paths.platformdirs, "user_data_path", user_data_path)

    assert paths.mcp_config_path() == tmp_path / "config" / "mcp_config.json"
    assert paths.data_dir() == tmp_path / "data"
    assert observed == [
        ("agent-utilities", "knuckles-team"),
        ("agent-utilities", "knuckles-team"),
    ]
