"""Required GraphOS API secrets from the shared XDG runtime configuration."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field

from agent_connector_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from agent_connector_sdk.credentials.resolution import resolve_secret_reference
from agent_utilities.core.config import setting

from graph_os.api.harness_context import BearerResolver

_PLAN_KEY_SETTING = "GRAPHOS_PLAN_SEAL_KEY_REF"
_CONTEXT_BEARER_SETTING = "GRAPHOS_CONTEXT_BEARER_REF"


@dataclass(frozen=True, slots=True)
class HostSecretPorts:
    """A startup-only seal key and a reference-only live MCP probe binding."""

    plan_seal_key: bytes = field(repr=False)
    context_bearer_ref: str
    resolve_bearer: BearerResolver


def _configured_reference(name: str) -> str:
    value = setting(name)
    try:
        return parse_secret_reference(value).render()
    except SecretReferenceError as exc:
        raise ValueError(f"{name} requires a runtime secret reference") from exc


async def _resolve_bearer(reference: str) -> str:
    """Keep OpenBao and environment resolution off the serving event loop."""

    return await asyncio.to_thread(resolve_secret_reference, reference)


def host_secret_ports_from_config() -> HostSecretPorts:
    """Require two XDG-backed references; never accept a plaintext config key.

    The plan key's referenced value is standard Base64 for exactly 32 random
    bytes. The MCP bearer is resolved only when the endpoint export probes the
    authenticated served boundary, so no token is retained in this bundle.
    """

    plan_ref = _configured_reference(_PLAN_KEY_SETTING)
    bearer_ref = _configured_reference(_CONTEXT_BEARER_SETTING)
    try:
        encoded = resolve_secret_reference(plan_ref)
        key = base64.b64decode(encoded, validate=True)
    except Exception:
        raise ValueError("GraphOS plan seal key is unavailable") from None
    if len(key) != 32:
        raise ValueError("GraphOS plan seal key must decode to 32 bytes")

    async def resolve_bound_bearer(reference: str) -> str:
        if reference != bearer_ref:
            raise ValueError("unconfigured MCP bearer reference")
        return await _resolve_bearer(bearer_ref)

    return HostSecretPorts(key, bearer_ref, resolve_bound_bearer)
