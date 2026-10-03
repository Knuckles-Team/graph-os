"""Admission contract tests with explicitly synthetic provider/error evidence."""

from __future__ import annotations

import json
import sys
from importlib.resources.abc import Traversable
from pathlib import Path
from types import ModuleType

import pytest

from graph_os.api.registry import EgMethod, PrincipalRule, Registry, eg_binding
from graph_os.api.registry.contract_admission import (
    _validate_registry_bindings,
    validate_contract_admission,
)
from graph_os.api.registry.eg_binding import EgContractError, load_eg_bindings
from tests.api.test_api_registry import make_op
from tests.api.test_eg_binding import _fixture


@pytest.fixture
def installed_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reuse loader fixtures at the installed-package resource resolution seam."""
    contract, exclusions = _fixture(tmp_path)
    real_files = eg_binding.resources.files

    def files(package: str) -> Traversable:
        if package == "epistemic_graph":
            return contract.parent
        return real_files(package)

    monkeypatch.setattr(eg_binding.resources, "files", files)
    monkeypatch.setattr(eg_binding, "_EXCLUSIONS", exclusions)
    return contract


def _registry() -> Registry:
    return Registry(load_eg_bindings())


def _change_method(contract: Path, **updates: object) -> None:
    path = contract / "methods.json"
    document = json.loads(path.read_text())
    document["methods"][0].update(updates)
    path.write_text(json.dumps(document))


def test_matching_binding_evidence_returns_none(installed_contract: Path) -> None:
    registry = _registry()
    before = (tuple(registry), registry.canonical, registry.digest)
    assert _validate_registry_bindings(registry) is None
    assert (tuple(registry), registry.canonical, registry.digest) == before


def test_curated_alias_and_composite_are_retained(installed_contract: Path) -> None:
    ops = list(load_eg_bindings())
    ops[0] = ops[0].model_copy(update={"id": "query.curated"})
    registry = Registry([*ops, make_op()])
    assert _validate_registry_bindings(registry) is None


def test_empty_registry_is_refused() -> None:
    with pytest.raises(EgContractError, match="empty API registry"):
        _validate_registry_bindings(Registry(()))


def test_missing_binding_is_not_silently_generated(installed_contract: Path) -> None:
    ops = load_eg_bindings()
    registry = Registry(ops[1:])
    with pytest.raises(EgContractError, match="missing EG bindings"):
        _validate_registry_bindings(registry)
    assert tuple(registry) == ops[1:]


def test_duplicate_method_ownership_is_refused(installed_contract: Path) -> None:
    ops = load_eg_bindings()
    duplicate = ops[0].model_copy(update={"id": "query.duplicate"})
    with pytest.raises(EgContractError, match="duplicate EG binding"):
        _validate_registry_bindings(Registry([*ops, duplicate]))


def test_stale_binding_is_refused(installed_contract: Path) -> None:
    ops = list(load_eg_bindings())
    ops[0] = ops[0].model_copy(update={"binding": EgMethod(service="Gone", op="Gone")})
    with pytest.raises(EgContractError, match="stale EG binding"):
        _validate_registry_bindings(Registry(ops))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"is_wire_callable": False}, "stale EG binding"),
        ({"is_wire_callable": None}, "missing is_wire_callable"),
        ({"policy": {"authz_action": "query:different"}}, "authority differs"),
        ({"policy": {}}, "cannot validate installed EG registry evidence"),
    ],
)
def test_changed_method_evidence_is_refused(
    installed_contract: Path, updates: dict, message: str
) -> None:
    registry = _registry()
    _change_method(installed_contract, **updates)
    with pytest.raises(EgContractError, match=message):
        _validate_registry_bindings(registry)


@pytest.mark.parametrize(
    "filename",
    [
        "methods.json",
        "scopes.json",
        "schemas/method.request.json",
        "schemas/result.query.json",
    ],
)
def test_missing_contract_file_is_refused(
    installed_contract: Path, filename: str
) -> None:
    registry = _registry()
    (installed_contract / filename).unlink()
    with pytest.raises(EgContractError):
        _validate_registry_bindings(registry)


@pytest.mark.parametrize("contents", [b"{", b"[]", b"\xff"])
def test_unreadable_method_contract_is_normalized(
    installed_contract: Path, contents: bytes
) -> None:
    registry = _registry()
    (installed_contract / "methods.json").write_bytes(contents)
    with pytest.raises(EgContractError):
        _validate_registry_bindings(registry)


def test_schema_change_after_construction_is_refused(installed_contract: Path) -> None:
    registry = _registry()
    path = installed_contract / "schemas/method.request.json"
    document = json.loads(path.read_text())
    document["methods"]["Read"] = {"type": "string"}
    path.write_text(json.dumps(document))
    with pytest.raises(EgContractError, match="canonical evidence changed"):
        _validate_registry_bindings(registry)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("canonical", b"{}", "canonical evidence changed"),
        ("digest", "invalid", "digest evidence changed"),
        ("api_version", "2", "canonical evidence changed"),
    ],
)
def test_changed_registry_evidence_is_refused(
    installed_contract: Path, field: str, value: object, message: str
) -> None:
    registry = _registry()
    setattr(registry, field, value)
    with pytest.raises(EgContractError, match=message):
        _validate_registry_bindings(registry)


def test_missing_installed_package_is_refused(
    installed_contract: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry()

    def missing_files(package: str) -> Path:
        raise ModuleNotFoundError(package)

    monkeypatch.setattr(eg_binding.resources, "files", missing_files)
    with pytest.raises(EgContractError, match="wheel is unavailable"):
        _validate_registry_bindings(registry)


def test_stale_exclusion_is_refused(installed_contract: Path) -> None:
    registry = _registry()
    eg_binding._EXCLUSIONS.write_text(
        "exclusions:\n  - method: Gone\n    reason: engine-internal\n"
    )
    with pytest.raises(EgContractError, match="stale EG exclusion"):
        _validate_registry_bindings(registry)


def test_reviewed_exclusion_uses_existing_loader(installed_contract: Path) -> None:
    eg_binding._EXCLUSIONS.write_text(
        "exclusions:\n  - method: Service\n    reason: service-only\n"
    )
    registry = _registry()
    assert len(registry) == 2
    assert _validate_registry_bindings(registry) is None


def test_service_only_binding_cannot_be_relaxed(installed_contract: Path) -> None:
    ops = [
        op.model_copy(update={"principals": PrincipalRule.ANY})
        if op.binding.service == "Service"
        else op
        for op in load_eg_bindings()
    ]
    with pytest.raises(EgContractError, match="service-only EG method exposed"):
        _validate_registry_bindings(Registry(ops))


@pytest.mark.parametrize("curated_alias", [False, True])
@pytest.mark.parametrize(
    "updates",
    [
        {"domain": "invalid-domain"},
        {"policy": {"authz_action": "query:read", "idempotent": True}},
        {
            "policy": {
                "authz_action": "query:read",
                "mutates": "false",
                "idempotent": True,
            }
        },
        {"replay_class": "Unknown"},
        {"stability": "unknown"},
        {"note": "   "},
        {"request_schema": None},
        {
            "request_schema": {
                "schema": "contract/schemas/method.request.json#/methods/Missing"
            }
        },
        {
            "result_schema": {
                "schema": "contract/schemas/result.query.json#/methods/Missing"
            }
        },
        {"result_schema": {"schema": "contract/schemas/missing.json#/methods/Read"}},
    ],
)
def test_bound_method_cannot_skip_provider_validation(
    installed_contract: Path, updates: dict, curated_alias: bool
) -> None:
    ops = list(load_eg_bindings())
    if curated_alias:
        ops = [op.model_copy(update={"id": f"alias.{op.id}"}) for op in ops]
    registry = Registry(ops)
    _change_method(installed_contract, **updates)
    # The same invalid evidence is already rejected during fresh generation.
    with pytest.raises(EgContractError):
        load_eg_bindings()
    with pytest.raises(EgContractError):
        _validate_registry_bindings(registry)


class _SyntheticMismatch(RuntimeError):
    """Test-only provider refusal; no production receipt algorithm is modeled."""


class _SyntheticMissing(_SyntheticMismatch):
    pass


@pytest.fixture
def startup_evidence(
    installed_contract: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, ModuleType, ModuleType]:
    """Explicitly synthetic modules; never generated or installed provider proof."""
    registry = _registry()
    errors = {
        "contract_version": 1,
        "errors": [
            {"code": "SYNTHETIC_REFUSAL", "http_status_hint": 409, "retryable": False}
        ],
    }
    (installed_contract / "errors.json").write_text(json.dumps(errors))
    pinned_bytes = {p: p.read_bytes() for p in installed_contract.rglob("*.json")}
    pin = "a" * 64

    def verify_receipt(expected: str) -> None:
        if expected != pin:
            raise _SyntheticMismatch("synthetic pin mismatch")
        for path, expected_bytes in pinned_bytes.items():
            if not path.exists():
                raise _SyntheticMissing("synthetic missing contract")
            if path.read_bytes() != expected_bytes:
                raise _SyntheticMismatch("synthetic contract content mismatch")

    provider = ModuleType("epistemic_graph.contract")
    provider.ContractDigestMismatch = _SyntheticMismatch
    provider.verify_receipt = verify_receipt
    generated = ModuleType("graph_os.api.generated.engine_errors")
    generated.REGISTRY_DIGEST = registry.digest
    generated.EG_RECEIPT_DIGEST = pin
    generated.ENGINE_ERRORS = {"SYNTHETIC_REFUSAL": (409, False)}
    monkeypatch.setitem(sys.modules, provider.__name__, provider)
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    return registry, generated, provider


def test_public_admission_accepts_matching_synthetic_evidence(startup_evidence) -> None:
    registry, _, _ = startup_evidence
    assert validate_contract_admission(registry) is None


@pytest.mark.parametrize(
    "symbol", ["REGISTRY_DIGEST", "EG_RECEIPT_DIGEST", "ENGINE_ERRORS"]
)
def test_missing_generated_symbol_preserves_cause(
    startup_evidence, symbol: str
) -> None:
    registry, generated, _ = startup_evidence
    delattr(generated, symbol)
    with pytest.raises(EgContractError) as caught:
        validate_contract_admission(registry)
    assert isinstance(caught.value.__cause__, AttributeError)


@pytest.mark.parametrize("symbol", ["REGISTRY_DIGEST", "EG_RECEIPT_DIGEST"])
@pytest.mark.parametrize("value", [None, "", "invalid", "A" * 64])
def test_malformed_generated_digest_fails(startup_evidence, symbol: str, value) -> None:
    registry, generated, _ = startup_evidence
    setattr(generated, symbol, value)
    with pytest.raises(EgContractError, match="SHA-256"):
        validate_contract_admission(registry)


def test_registry_identity_mismatch_fails(startup_evidence) -> None:
    registry, generated, _ = startup_evidence
    generated.REGISTRY_DIGEST = "b" * 64
    with pytest.raises(EgContractError, match="registry differs"):
        validate_contract_admission(registry)


@pytest.mark.parametrize("failure", [_SyntheticMismatch, _SyntheticMissing])
def test_provider_refusal_preserves_exact_cause(startup_evidence, failure) -> None:
    registry, _, provider = startup_evidence
    cause = failure("synthetic provider evidence failure")

    def refuse(pin: str) -> None:
        raise cause

    provider.verify_receipt = refuse
    with pytest.raises(EgContractError) as caught:
        validate_contract_admission(registry)
    assert caught.value.__cause__ is cause


def test_unrelated_provider_runtime_failure_is_not_reclassified(
    startup_evidence,
) -> None:
    registry, _, provider = startup_evidence
    cause = RuntimeError("provider implementation bug")

    def broken(pin: str) -> None:
        raise cause

    provider.verify_receipt = broken
    with pytest.raises(RuntimeError) as caught:
        validate_contract_admission(registry)
    assert caught.value is cause


def test_valid_semantic_mutates_flip_requires_pinned_evidence(
    installed_contract: Path, startup_evidence
) -> None:
    registry, _, _ = startup_evidence
    _change_method(
        installed_contract,
        policy={"authz_action": "query:read", "mutates": True, "idempotent": True},
    )
    # Both shape checks accept this valid new policy; trusted provider evidence
    # must reject it against the generator's original pin before serving.
    load_eg_bindings()
    assert _validate_registry_bindings(registry) is None
    with pytest.raises(EgContractError) as caught:
        validate_contract_admission(registry)
    assert isinstance(caught.value.__cause__, _SyntheticMismatch)


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"OTHER": (409, False)},
        {"SYNTHETIC_REFUSAL": (403, False)},
        {"SYNTHETIC_REFUSAL": (409, True)},
        {"SYNTHETIC_REFUSAL": (True, False)},
        {"SYNTHETIC_REFUSAL": (409, 0)},
        {"SYNTHETIC_REFUSAL": [409, False]},
        {"SYNTHETIC_REFUSAL": (409, False), "EXTRA": (500, True)},
    ],
)
def test_generated_error_evidence_must_match_exactly(startup_evidence, mapping) -> None:
    registry, generated, _ = startup_evidence
    generated.ENGINE_ERRORS = mapping
    with pytest.raises(EgContractError):
        validate_contract_admission(registry)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [None],
        [{"code": "X", "http_status_hint": 409}],
        [{"code": "X", "http_status_hint": True, "retryable": False}],
        [{"code": "X", "http_status_hint": 409, "retryable": 0}],
        [{"code": "X", "http_status_hint": 409, "retryable": False}] * 2,
    ],
)
def test_malformed_installed_error_rows_fail_even_if_provider_verifies(
    installed_contract: Path, startup_evidence, rows
) -> None:
    registry, _, provider = startup_evidence
    # Isolate error-table validation from the independently tested verifier seam.
    provider.verify_receipt = lambda pin: None
    (installed_contract / "errors.json").write_text(
        json.dumps({"contract_version": 1, "errors": rows})
    )
    with pytest.raises(EgContractError):
        validate_contract_admission(registry)


@pytest.mark.parametrize(
    "module", ["graph_os.api.generated.engine_errors", "epistemic_graph.contract"]
)
def test_missing_contract_module_fails_closed(
    startup_evidence, monkeypatch, module
) -> None:
    registry, _, _ = startup_evidence
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(EgContractError) as caught:
        validate_contract_admission(registry)
    assert isinstance(caught.value.__cause__, ImportError)


def test_wrong_generated_provider_pin_is_refused(startup_evidence) -> None:
    registry, generated, _ = startup_evidence
    generated.EG_RECEIPT_DIGEST = "b" * 64
    with pytest.raises(EgContractError) as caught:
        validate_contract_admission(registry)
    assert isinstance(caught.value.__cause__, _SyntheticMismatch)


def test_missing_error_file_preserves_provider_missing_failure(
    installed_contract: Path, startup_evidence
) -> None:
    registry, _, _ = startup_evidence
    (installed_contract / "errors.json").unlink()
    with pytest.raises(EgContractError) as caught:
        validate_contract_admission(registry)
    assert isinstance(caught.value.__cause__, _SyntheticMissing)


@pytest.mark.parametrize("version", [None, True, "1", 2])
def test_unsupported_error_contract_version_fails(
    installed_contract: Path, startup_evidence, version
) -> None:
    registry, _, provider = startup_evidence
    provider.verify_receipt = lambda pin: None
    path = installed_contract / "errors.json"
    document = json.loads(path.read_text())
    document["contract_version"] = version
    path.write_text(json.dumps(document))
    with pytest.raises(EgContractError, match="unsupported engine error contract"):
        validate_contract_admission(registry)
