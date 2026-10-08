"""Focused contract generation checks; no repository-wide scan required."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/gen_api.py"
SPEC = importlib.util.spec_from_file_location("gen_api", SCRIPT)
assert SPEC and SPEC.loader
gen_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen_api)


def _registry() -> dict:
    return {
        "api_version": "1",
        "registry_digest": "a" * 64,
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


@pytest.fixture
def engine_contract(tmp_path, monkeypatch):
    from graph_os.api.registry import eg_binding

    contract = tmp_path / "contract"
    contract.mkdir()
    (contract / "errors.json").write_text(
        json.dumps(
            {
                "contract_version": 1,
                "errors": [
                    {"code": "SYNTHETIC", "http_status_hint": 409, "retryable": False}
                ],
            }
        )
    )
    provider = ModuleType("epistemic_graph.contract")
    provider.RECEIPT_DIGEST = "b" * 64
    calls = []
    provider.verify_receipt = calls.append
    exceptions = ModuleType("epistemic_graph.contract_errors")
    exceptions.ContractDigestMismatch = type("SyntheticMismatch", (RuntimeError,), {})
    monkeypatch.setitem(sys.modules, provider.__name__, provider)
    monkeypatch.setitem(sys.modules, exceptions.__name__, exceptions)
    monkeypatch.setattr(eg_binding.resources, "files", lambda package: tmp_path)
    return provider, contract, calls


def test_generator_emits_deterministic_artifacts(engine_contract) -> None:
    first = gen_api.generate(_registry())
    assert first == gen_api.generate(_registry())
    openapi = json.loads(first[gen_api.ROOT / "docs/api/openapi.json"])
    assert (
        openapi["paths"]["/api/v1/ops/query.uql"]["post"]["operationId"] == "query.uql"
    )
    assert (
        json.loads(first[gen_api.GENERATED / "registry.json"])["registry_digest"]
        == "a" * 64
    )
    models = first[gen_api.ROOT / "graph_os/client/_generated_models.py"]
    assert len(models.splitlines()) < 900
    compile(models, "_generated_models.py", "exec")
    compile(first[gen_api.ROOT / "graph_os/client/invoke.py"], "invoke.py", "exec")


def test_generator_fails_closed_on_an_empty_registry() -> None:
    with pytest.raises(ValueError, match="empty API registry"):
        gen_api.generate({"ops": []})


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


def _eg_compat_registry(root: Path, *, required_limit: bool, answer: bool) -> dict:
    root.mkdir(parents=True)
    request = {
        "$defs": {
            "Params": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["text", "limit"] if required_limit else ["text"],
            }
        },
        "methods": {"Uql": {"properties": {"params": {"$ref": "#/$defs/Params"}}}},
    }
    response = {
        "$defs": {
            "Result": {
                "type": "object",
                "properties": {"answer": {"type": "string"}} if answer else {},
            }
        },
        "methods": {
            "Uql": {"bodies": {"result": {"schema": {"$ref": "#/$defs/Result"}}}}
        },
    }
    registry = _registry()
    registry["ops"][0]["stability"] = "stable"
    for field, name, document in (
        ("params", "method.request.json", request),
        ("result", "result.query.json", response),
    ):
        path = root / name
        path.write_text(json.dumps(document))
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        registry["ops"][0][field] = {
            "eg_schema": f"contract/schemas/{name}#/methods/Uql",
            "schema_sha256": hashlib.sha256(canonical).hexdigest(),
        }
    gen_api._snapshot_eg_schemas(registry, root.parent)
    return registry


def test_compat_resolves_eg_refs_and_detects_nested_breaks(tmp_path: Path) -> None:
    baseline = _eg_compat_registry(
        tmp_path / "before" / "schemas", required_limit=False, answer=True
    )
    candidate = _eg_compat_registry(
        tmp_path / "after" / "schemas", required_limit=True, answer=False
    )
    changes = gen_api._breaking_changes(baseline, candidate)
    assert any("new required params params.params" in change for change in changes)
    assert any("removed result fields" in change for change in changes)


def test_compat_fails_closed_when_eg_schema_hash_is_stale(tmp_path: Path) -> None:
    registry = _eg_compat_registry(
        tmp_path / "contract" / "schemas", required_limit=False, answer=True
    )
    registry["ops"][0]["params"]["schema_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="EG schema digest changed"):
        gen_api._snapshot_eg_schemas(registry, tmp_path / "contract")


def test_compat_requires_review_for_unclassified_eg_schema_change(
    tmp_path: Path,
) -> None:
    baseline = _eg_compat_registry(
        tmp_path / "contract" / "schemas", required_limit=False, answer=True
    )
    candidate = json.loads(json.dumps(baseline))
    candidate["ops"][0]["params"]["schema_sha256"] = "1" * 64
    assert "review required" in " ".join(gen_api._breaking_changes(baseline, candidate))


def test_engine_emitter_binds_exact_provider_and_registry(engine_contract):
    provider, _, calls = engine_contract
    output = gen_api.generate(_registry())[gen_api.GENERATED / "engine_errors.py"]
    namespace = {}
    exec(compile(output, "engine_errors.py", "exec"), namespace)
    assert namespace["REGISTRY_DIGEST"] == _registry()["registry_digest"]
    assert namespace["EG_RECEIPT_DIGEST"] == provider.RECEIPT_DIGEST
    assert namespace["ENGINE_ERRORS"] == {"SYNTHETIC": (409, False)}
    assert calls == [provider.RECEIPT_DIGEST, provider.RECEIPT_DIGEST]


def test_engine_emitter_refuses_absent_error_contract(engine_contract):
    _, contract, _ = engine_contract
    (contract / "errors.json").unlink()
    with pytest.raises(ValueError):
        gen_api.generate(_registry())
