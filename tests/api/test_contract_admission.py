"""Partial registry admission checks; generated error/pin admission is pending."""

from __future__ import annotations

import json
from importlib.resources.abc import Traversable
from pathlib import Path

import pytest

from graph_os.api.registry import EgMethod, PrincipalRule, Registry, eg_binding
from graph_os.api.registry.contract_admission import _validate_registry_bindings
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
