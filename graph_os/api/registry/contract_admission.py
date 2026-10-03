"""Fail-closed startup admission against generated and installed EG evidence."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

from .digest import canonical_registry, registry_digest
from .eg_binding import (
    EgContractError,
    _contract_root,
    _load_scopes,
    _method_op,
    _read_json,
    load_eg_bindings,
)
from .registry import Registry
from .spec import EgMethod


def _require_digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EgContractError("generated contract digest must be SHA-256 hex")
    return value


def _verify_provider_receipt(pin: str) -> None:
    from epistemic_graph.contract_errors import ContractDigestMismatch

    try:
        from epistemic_graph.contract import verify_receipt

        verify_receipt(pin)
    except ContractDigestMismatch as exc:
        # The provider's missing-contract exception subclasses this type too.
        raise EgContractError(
            "installed EG contract does not match generated pin"
        ) from exc


def _error_entry(code: object, status: object, retryable: object) -> tuple[int, bool]:
    if not isinstance(code, str) or not code or code != code.strip():
        raise EgContractError("invalid engine error code")
    if type(status) is not int or not 100 <= status <= 599:
        raise EgContractError("invalid engine error HTTP status")
    if type(retryable) is not bool:
        raise EgContractError("invalid engine error retryability")
    return status, retryable


def _installed_error_table() -> dict[str, tuple[int, bool]]:
    document = _read_json(_contract_root(None) / "errors.json")
    if (
        type(document.get("contract_version")) is not int
        or document["contract_version"] != 1
    ):
        raise EgContractError("unsupported engine error contract version")
    rows = document.get("errors")
    if not isinstance(rows, list) or not rows:
        raise EgContractError("engine error contract requires nonempty rows")
    actual: dict[str, tuple[int, bool]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EgContractError("engine error row must be an object")
        code = row.get("code")
        entry = _error_entry(code, row.get("http_status_hint"), row.get("retryable"))
        if code in actual:
            raise EgContractError("duplicate engine error code")
        actual[code] = entry
    return actual


def _validate_error_evidence(expected: object) -> None:
    if not isinstance(expected, dict) or not expected:
        raise EgContractError("generated engine error table is missing or malformed")
    for code, entry in expected.items():
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise EgContractError("generated engine error entry must be a pair")
        _error_entry(code, *entry)
    if _installed_error_table() != expected:
        raise EgContractError("installed engine errors differ from generated evidence")


def validate_contract_admission(registry: Registry) -> None:
    """Admit only a registry bound to the generated, pinned provider contract.

    The composition root must call this before constructing or mounting the API.
    The provider owns receipt authentication and installed-file verification;
    shape validation alone cannot detect valid-but-changed method semantics.
    Missing generated artifacts fail closed. No source-tree or fixture fallback
    is available in production. A qualified provider verifier is a prerequisite.
    """
    try:
        evidence = importlib.import_module("graph_os.api.generated.engine_errors")
        expected_registry = _require_digest(evidence.REGISTRY_DIGEST)
        pin = _require_digest(evidence.EG_RECEIPT_DIGEST)
        if registry.digest != expected_registry:
            raise EgContractError("registry differs from generated contract identity")
        _verify_provider_receipt(pin)
        _validate_registry_bindings(registry)
        _validate_error_evidence(evidence.ENGINE_ERRORS)
    except EgContractError:
        raise
    except (
        ImportError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        raise EgContractError("cannot validate startup contract evidence") from exc


def _validate_bound_method_contracts(registry: Registry) -> None:
    """Reuse generation validation for methods suppressed by curated bindings.

    ``load_eg_bindings`` checks curated ownership but only builds unbound methods.
    A constructed registry suppresses every covered method, so validate those
    provider rows with the same builder too. Do not infer curated declarations
    from their names or replace their caller-facing schemas with generated ones.
    """

    root = _contract_root(None)
    scopes = _load_scopes(root)
    methods = _read_json(root / "methods.json")["methods"]
    bound = {op.binding.service for op in registry if isinstance(op.binding, EgMethod)}
    schema_documents: dict[Path, dict[str, Any]] = {}
    for row in methods:
        if row["id"] in bound:
            _method_op(row, root, scopes, schema_documents)


def _validate_registry_bindings(registry: Registry) -> None:
    """Recheck a constructed registry against the installed package authority.

    The existing loader owns binding, scope, wire-callability and exclusion checks.
    Treat the constructed operations as curated so their identities and authority
    are checked too; any remaining generated operation represents missing coverage.
    Reuse the method builder to validate provider rows even for curated bindings.
    Canonicalization checks schema references and detects changes since construction.
    This does not compare against a generated registry or engine error/pin evidence.
    Fixture resource resolution belongs in tests, never in this callable's API.
    """

    if not registry:
        raise EgContractError("cannot admit an empty API registry")
    try:
        missing = load_eg_bindings(curated_ops=registry)
        if missing:
            raise EgContractError(
                "registry is missing EG bindings: " + ", ".join(op.id for op in missing)
            )
        _validate_bound_method_contracts(registry)
        current = canonical_registry(registry, api_version=registry.api_version)
        if registry.canonical != current:
            raise EgContractError("registry canonical evidence changed")
        if registry.digest != registry_digest(
            registry, api_version=registry.api_version
        ):
            raise EgContractError("registry digest evidence changed")
    except EgContractError:
        raise
    except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
        raise EgContractError("cannot validate installed EG registry evidence") from exc
