"""Registry property test for GRAPHOS-IDENTITY-R002.

GraphOS's own domain scopes must use the shared scope classes and stay
consistent with the single generated engine scope registry rather than a
separately maintained list. This asserts every scope GraphOS declares in
``graph_os.api.policy.domain_scopes`` matches a fixture contract's name and
class when they agree, and that a declaration drifting from the registry
(wrong class, or a name the registry no longer carries) is caught rather
than silently accepted. The fixture mirrors ``tests/api/test_eg_binding.py``
rather than reading the installed ``epistemic_graph`` wheel directly: that
wheel's packaged ``contract/`` resource is not guaranteed to carry the full
``methods.json``/``scopes.json``/``schemas`` set in every test environment,
only the pinned production build does, so every other contract test in this
suite is already fixture-based for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_os.api.policy.domain_scopes import (
    GRAPHOS_DOMAIN_SCOPES,
    SCOPE_CLASSES,
    DomainScope,
    generated_registry_mismatches,
    scopes_by_name,
)
from tests.api.test_eg_binding import _fixture


def test_every_declared_scope_uses_a_shared_scope_class() -> None:
    for scope in GRAPHOS_DOMAIN_SCOPES:
        assert scope.scope_class in SCOPE_CLASSES


def test_rejects_an_unknown_scope_class() -> None:
    with pytest.raises(ValueError, match="unknown scope class"):
        DomainScope("fleet:events", "superadmin")


def test_scopes_by_name_indexes_every_declared_scope() -> None:
    indexed = scopes_by_name()
    assert set(indexed) == {scope.name for scope in GRAPHOS_DOMAIN_SCOPES}
    assert indexed["webui:admin"].scope_class == "admin"


@pytest.mark.spec("GRAPHOS-IDENTITY-R002")
def test_declared_scopes_match_a_consistent_registry(tmp_path: Path) -> None:
    contract, _exclusions = _fixture(tmp_path)
    scope_path = contract / "scopes.json"
    document = json.loads(scope_path.read_text())
    document["scopes"].extend(
        {"scope": scope.name, "class": scope.scope_class}
        for scope in GRAPHOS_DOMAIN_SCOPES
    )
    scope_path.write_text(json.dumps(document))

    mismatches = generated_registry_mismatches(contract_root=contract)

    assert mismatches == {}


def test_a_class_drift_from_the_registry_is_reported(tmp_path: Path) -> None:
    contract, _exclusions = _fixture(tmp_path)
    scope_path = contract / "scopes.json"
    document = json.loads(scope_path.read_text())
    document["scopes"].append({"scope": "fleet:events", "class": "admin"})
    scope_path.write_text(json.dumps(document))

    mismatches = generated_registry_mismatches(contract_root=contract)

    assert "fleet:events" in mismatches
    assert "service-only" in mismatches["fleet:events"]
    assert "admin" in mismatches["fleet:events"]


def test_a_scope_missing_from_the_registry_is_reported(tmp_path: Path) -> None:
    contract, _exclusions = _fixture(tmp_path)

    mismatches = generated_registry_mismatches(contract_root=contract)

    assert mismatches == {
        scope.name: "missing from the generated scope registry"
        for scope in GRAPHOS_DOMAIN_SCOPES
    }
