"""Immutable observation of one served child's native MCP discovery families.

This is evidence for a future catalog reconciler, not an EG pack binding.
The latter also needs a configuration revision and an authorization partition
from the serving authority; neither can be inferred from these observations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


class CatalogSnapshotUnavailable(RuntimeError):
    """A complete, stable child observation is unavailable."""


@dataclass(frozen=True, slots=True)
class McpCatalogAttestation:
    """Source fields for EG reconciliation, without caller authority or CAS."""

    server_name: str
    attester_principal_id: str
    component_id: str
    component_revision: int
    component_digest: str
    registry_revision: int
    registry_digest: str
    registration_config_digest: str
    four_family_digest: str
    child_id: str
    discovery_tenant: str
    local_catalog_epoch: int
    child_connection_generation: int


def registration_config_digest(url: str, resources: tuple[tuple[str, str], ...]) -> str:
    """Digest only RegisterServer fields used by the served child projection."""
    if not url:
        raise CatalogSnapshotUnavailable("server registration endpoint is unavailable")
    try:
        canonical = json.dumps(
            {"url": url, "resources": dict(resources)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CatalogSnapshotUnavailable(
            "server registration is not canonical JSON"
        ) from exc
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ChildCatalogSnapshot:
    server_name: str
    component_id: str
    registry_revision: int
    registry_digest: str
    registration_config_digest: str
    component_revision: int
    component_digest: str
    discovery_tenant: str
    local_catalog_epoch: int
    child_id: str
    child_connection_generation: int
    canonical_json: bytes
    content_digest: str

    @classmethod
    def from_observation(
        cls,
        *,
        server_name: str,
        component_id: str,
        registry_revision: int | None,
        registry_digest: str | None,
        registration_config_digest: str | None,
        component_revision: int | None,
        component_digest: str | None,
        discovery_tenant: str | None,
        local_catalog_epoch: int,
        child_id: str,
        child_connection_generation: int,
        tools: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        resource_templates: list[dict[str, Any]],
        prompts: list[dict[str, Any]],
        family_errors: dict[str, str],
    ) -> ChildCatalogSnapshot:
        if (
            registry_revision is None
            or registry_revision < 1
            or not registry_digest
            or not registration_config_digest
        ):
            raise CatalogSnapshotUnavailable(
                "EG fleet registry identity is unavailable"
            )
        if (
            component_revision is None
            or component_revision < 1
            or not component_digest
            or not component_id
            or not discovery_tenant
        ):
            raise CatalogSnapshotUnavailable(
                "served configuration or verified discovery authority is unavailable"
            )
        if any(reason != "unsupported" for reason in family_errors.values()):
            raise CatalogSnapshotUnavailable("native MCP discovery is incomplete")
        if not child_id or child_connection_generation < 1 or local_catalog_epoch < 1:
            raise CatalogSnapshotUnavailable(
                "mounted child identity or generation is unavailable"
            )
        try:

            def ordered(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return sorted(
                    entries,
                    key=lambda entry: json.dumps(
                        entry,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                )

            canonical = json.dumps(
                {
                    "tools": ordered(tools),
                    "resources": ordered(resources),
                    "resource_templates": ordered(resource_templates),
                    "prompts": ordered(prompts),
                    "unsupported_families": sorted(family_errors),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CatalogSnapshotUnavailable(
                "native MCP discovery is not canonical JSON"
            ) from exc
        return cls(
            server_name=server_name,
            component_id=component_id,
            registry_revision=registry_revision,
            registry_digest=registry_digest,
            registration_config_digest=registration_config_digest,
            component_revision=component_revision,
            component_digest=component_digest,
            discovery_tenant=discovery_tenant,
            local_catalog_epoch=local_catalog_epoch,
            child_id=child_id,
            child_connection_generation=child_connection_generation,
            canonical_json=canonical,
            content_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
        )

    def matches_source(
        self,
        *,
        component_id: str,
        registry_revision: int,
        registry_digest: str,
        registration_config_digest: str,
        component_revision: int,
        component_digest: str,
        discovery_tenant: str,
        local_catalog_epoch: int,
        child_id: str,
        child_connection_generation: int,
    ) -> bool:
        """Require the exact config, tenant and mounted transport observed."""
        return (
            self.component_id == component_id
            and self.registry_revision == registry_revision
            and self.registry_digest == registry_digest
            and self.registration_config_digest == registration_config_digest
            and self.component_revision == component_revision
            and self.component_digest == component_digest
            and self.discovery_tenant == discovery_tenant
            and self.local_catalog_epoch == local_catalog_epoch
            and self.child_id == child_id
            and self.child_connection_generation == child_connection_generation
        )


__all__ = [
    "CatalogSnapshotUnavailable",
    "ChildCatalogSnapshot",
    "McpCatalogAttestation",
    "registration_config_digest",
]
