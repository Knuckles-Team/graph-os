"""Doctor coverage checks expose aggregate health, never deployment identities."""

from __future__ import annotations

import json
from typing import Any

from graph_os.deployment import doctor as D

_LOCAL_PATH = "/private/home/agent-user/workspace/workspace.yml"
_PERSON = "Ada Lovelace"
_ENDPOINT = "https://private.example.invalid/graphql"
_REPOSITORY = "customer-secret-repository"
_STALE_REPOSITORY = "person-owned-repository"
_PRIVATE_VALUES = {
    _LOCAL_PATH,
    _PERSON,
    _ENDPOINT,
    _REPOSITORY,
    _STALE_REPOSITORY,
}


def _assert_private_values_absent(result: dict[str, Any]) -> None:
    detail = json.dumps(result["detail"], sort_keys=True)
    data = json.dumps(result["data"], sort_keys=True)
    report = json.dumps({"checks": [result]}, sort_keys=True)
    for value in _PRIVATE_VALUES:
        assert value not in detail
        assert value not in data
        assert value not in report
    assert result["data"]["redacted"] is True


def test_workspace_config_doctor_redacts_manifest_and_validation_details(
    monkeypatch,
) -> None:
    from agent_utilities.core import workspace_config

    monkeypatch.setattr(
        workspace_config,
        "validate_workspace_yml",
        lambda: {
            "found": True,
            "path": _LOCAL_PATH,
            "parsed": True,
            "errors": [
                f"{_PERSON} configured {_ENDPOINT} for {_REPOSITORY} at {_LOCAL_PATH}"
            ],
            "warnings": [f"{_STALE_REPOSITORY} is owned by {_PERSON}"],
            "repo_count": 2,
        },
    )

    result = D._check_workspace_config()

    assert result["status"] == "fail"
    assert result["data"] == {
        "found": True,
        "parsed": True,
        "repo_count": 2,
        "error_count": 1,
        "warning_count": 1,
        "redacted": True,
    }
    assert "path" not in result["data"]
    _assert_private_values_absent(result)
