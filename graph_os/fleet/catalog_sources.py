"""Verified fleet, live protocol, and SDK entries as one catalog source."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from graph_os.fleet.catalog_items import (
    CatalogItem,
    CredentialMode,
    connector_items,
    items_from_child_probe,
    items_from_eg_catalog,
)

VerifiedRead = Callable[[], Awaitable[Any]]
LiveRead = Callable[[], Awaitable[Mapping[str, Mapping[str, Any]]]]
SdkRead = Callable[[], Awaitable[Iterable[Mapping[str, Any]]]]


@dataclass(frozen=True, slots=True)
class _ServerPolicy:
    required_scopes: frozenset[str]
    executor_scopes: frozenset[str]
    credential_mode: CredentialMode
    fleet_effects: Mapping[str, str]


def _verified_server_policy(body: bytes) -> _ServerPolicy:
    """Read only engine-verified server content, never live probe metadata."""
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("fleet manifest exceeds boundary")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("fleet manifest is not JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("fleet manifest must be an object")
    nested = decoded.get("config", decoded.get("mcpServer", decoded))
    if not isinstance(nested, dict):
        raise ValueError("fleet manifest config must be an object")
    raw_scopes = _scopes(nested.get("required_scopes", ()), "required_scopes")
    executor_scopes = _scopes(nested.get("executor_scopes", ()), "executor_scopes")
    mode = nested.get("credential_mode", "delegated")
    if not isinstance(mode, str) or mode not in {"delegated", "service"}:
        raise ValueError("fleet credential_mode is invalid")
    if mode == "service" and not raw_scopes:
        raise ValueError("service child requires declared domain scopes")
    if mode == "service" and not executor_scopes:
        raise ValueError("service child requires executor_scopes")
    effects = nested.get("fleet_effects", {})
    if (
        not isinstance(effects, dict)
        or len(effects) > 2_048
        or any(
            not isinstance(name, str)
            or not 1 <= len(name) <= 256
            or not isinstance(value, str)
            or value not in {"read", "write", "destructive", "admin"}
            for name, value in effects.items()
        )
    ):
        raise ValueError("fleet_effects are invalid")
    return _ServerPolicy(
        raw_scopes, executor_scopes, cast(CredentialMode, mode), effects
    )


def _scopes(value: Any, field_name: str) -> frozenset[str]:
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, list | tuple) or any(
        not isinstance(scope, str) or not 1 <= len(scope) <= 128 for scope in value
    ):
        raise ValueError(f"fleet {field_name} are invalid")
    return frozenset(value)


class CombinedFleetSource:
    """Only EG-admitted live servers may contribute probed protocol rows.

    EG owns component identity and body. A child probe may fill a tool schema
    or advertise a protocol family that EG does not model, but can never make
    an unregistered server visible. SDK entries arrive through a separate
    typed adapter; the caller supplies its verified pack reader.
    """

    def __init__(self, *, verified: VerifiedRead, live: LiveRead, sdk: SdkRead):
        self._verified = verified
        self._live = live
        self._sdk = sdk

    async def __call__(self) -> tuple[CatalogItem, ...]:
        snapshot = await self._verified()
        admitted = {
            server.component.server_name: _verified_server_policy(server.content.body)
            for server in snapshot.servers
            if server.registration is not None
        }
        subjects = {
            server.component.server_name: getattr(
                server.component, "component_id", None
            )
            for server in snapshot.servers
            if server.registration is not None
        }
        if any(
            not isinstance(subjects[name], str) or not subjects[name]
            for name, policy in admitted.items()
            if policy.credential_mode == "service"
        ):
            raise ValueError("service child lacks verified EG subject_id")
        merged = {
            item.id: replace(
                item,
                required_scopes=admitted[item.server].required_scopes,
                credential_mode=admitted[item.server].credential_mode,
                executor_scopes=admitted[item.server].executor_scopes,
                subject_id=subjects[item.server],
                effect_override=admitted[item.server].fleet_effects.get(item.name),
            )
            for item in items_from_eg_catalog(snapshot)
            if item.server in admitted
        }
        probe = await self._live()
        for server in admitted:
            for item in items_from_child_probe(server, probe.get(server, {})):
                previous = merged.get(item.id)
                policy = admitted[server]
                if previous is None:
                    merged[item.id] = replace(
                        item,
                        required_scopes=policy.required_scopes,
                        credential_mode=policy.credential_mode,
                        executor_scopes=policy.executor_scopes,
                        subject_id=subjects[server],
                        effect_override=policy.fleet_effects.get(item.name),
                    )
                elif item.kind == "tool":
                    merged[item.id] = replace(
                        previous,
                        schema=item.schema,
                        annotations=item.annotations,
                    )
        for item in connector_items(await self._sdk()):
            if item.id in merged:
                raise ValueError(f"duplicate connector item: {item.id}")
            merged[item.id] = item
        return tuple(merged[name] for name in sorted(merged))
