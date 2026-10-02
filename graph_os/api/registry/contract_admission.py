"""Registry evidence checks for startup contract admission.

This is the independently usable validation portion, not a public startup
admission entrypoint. Admission also needs generated engine error evidence and
the provider receipt pin; their generator interface is not yet available.
Passing this check alone must not authorize serving the API.
"""

from __future__ import annotations

from .digest import canonical_registry, registry_digest
from .eg_binding import EgContractError, load_eg_bindings
from .registry import Registry


def _validate_registry_bindings(registry: Registry) -> None:
    """Recheck a constructed registry against the installed package authority.

    The existing loader owns binding, scope, wire-callability and exclusion checks.
    Treat the constructed operations as curated so their identities and authority
    are checked too; any remaining generated operation represents missing coverage.
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
