"""Registry evidence checks for startup contract admission.

This is the independently usable validation portion, not a public startup
admission entrypoint. Admission also needs generated engine error evidence and
the provider receipt pin; their generator interface is not yet available.
Passing this check alone must not authorize serving the API.
"""

from __future__ import annotations

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
