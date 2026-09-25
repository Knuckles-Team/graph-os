"""Focused contract generation checks; no repository-wide scan required."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/gen_api.py"
SPEC = importlib.util.spec_from_file_location("gen_api", SCRIPT)
assert SPEC and SPEC.loader
gen_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen_api)


def _registry() -> dict:
    return {
        "api_version": "1",
        "registry_digest": "fixture-digest",
        "ops": [
            {
                "id": "query.uql",
                "verb": "ask",
                "summary": "Run UQL",
                "examples": ["ask the graph"],
                "params": {"schema": {"type": "object"}},
                "result": {"schema": {"type": "object"}},
                "http": None,
            }
        ],
    }


def test_generator_emits_deterministic_artifacts_and_engine_map(tmp_path: Path) -> None:
    catalog = tmp_path / "errors.json"
    catalog.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "errors": [
                    {"code": "READ_ONLY", "http_status_hint": 403, "retryable": False},
                    {"code": "REDIRECTED", "http_status_hint": 503, "retryable": True},
                ],
            }
        )
    )
    first = gen_api.generate(_registry(), catalog)
    assert first == gen_api.generate(_registry(), catalog)
    assert b"'REDIRECTED': (503, True)" in first[gen_api.GENERATED / "engine_errors.py"]
    openapi = json.loads(first[gen_api.ROOT / "docs/api/openapi.json"])
    assert (
        openapi["paths"]["/api/v1/ops/query.uql"]["post"]["operationId"] == "query.uql"
    )
    assert (
        json.loads(first[gen_api.GENERATED / "registry.json"])["registry_digest"]
        == "fixture-digest"
    )


def test_generator_fails_closed_on_missing_or_empty_inputs(tmp_path: Path) -> None:
    catalog = tmp_path / "errors.json"
    catalog.write_text('{"contract_version": 1, "errors": []}')
    with pytest.raises(ValueError, match="empty API registry"):
        gen_api.generate({"ops": []}, catalog)
    with pytest.raises(ValueError, match="empty EG error map"):
        gen_api.generate(_registry(), catalog)
    with pytest.raises(FileNotFoundError):
        gen_api.generate(_registry(), tmp_path / "missing.json")


def test_generator_rejects_duplicate_engine_codes(tmp_path: Path) -> None:
    catalog = tmp_path / "errors.json"
    error = {"code": "READ_ONLY", "http_status_hint": 403, "retryable": False}
    catalog.write_text(json.dumps({"contract_version": 1, "errors": [error, error]}))
    with pytest.raises(ValueError, match="duplicate EG error code"):
        gen_api.generate(_registry(), catalog)
