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
                    {"code": "REDIRECTED", "http_status_hint": 307, "retryable": True},
                ],
            }
        )
    )
    first = gen_api.generate(_registry(), catalog)
    assert first == gen_api.generate(_registry(), catalog)
    assert b'"REDIRECTED": (307, True)' in first[gen_api.GENERATED / "engine_errors.py"]
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


def test_compat_detects_stable_breaks_but_allows_additive_changes() -> None:
    before = _registry()
    before["ops"][0]["stability"] = "stable"
    before["ops"][0]["params"]["schema"] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    before["ops"][0]["result"]["schema"] = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
    }
    after = json.loads(json.dumps(before))
    after["ops"][0]["params"]["schema"]["properties"]["limit"] = {"type": "integer"}
    assert gen_api._breaking_changes(before, after) == []
    after["ops"][0]["params"]["schema"]["required"].append("limit")
    after["ops"][0]["result"]["schema"]["properties"].pop("answer")
    changes = gen_api._breaking_changes(before, after)
    assert any("new required params" in row for row in changes)
    assert any("removed result fields" in row for row in changes)


def test_openapi_embeds_eg_schema_and_rewrites_local_refs(tmp_path: Path) -> None:
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "method.request.json").write_text(
        json.dumps(
            {
                "$defs": {"Payload": {"type": "object"}},
                "methods": {"Create": {"$ref": "#/$defs/Payload"}},
            }
        )
    )
    registry = _registry()
    registry["ops"][0]["params"] = {
        "eg_schema": "contract/schemas/method.request.json#/methods/Create"
    }
    result = gen_api._openapi(registry, tmp_path)
    request = result["paths"]["/api/v1/ops/query.uql"]["post"]["requestBody"]
    assert request["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EgMethodRequest/methods/Create"
    }
    assert result["components"]["schemas"]["EgMethodRequest"]["methods"]["Create"] == {
        "$ref": "#/components/schemas/EgMethodRequest/$defs/Payload"
    }
