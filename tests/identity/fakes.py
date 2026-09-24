"""In-memory doubles for the engine identity port and the secrets backend.

The port double answers the engine's reply shapes (``{"kind", "value"}``) for
the ops the external authorities send, and records every op so a test can
assert exactly what reached the engine -- or that nothing did.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from graph_os.identity.idp_common import IdentityRefused

Handler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class FakeSecrets:
    """The atomic subset of AU's ``SecretsClient`` over a dict."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, **metadata: Any) -> None:
        self.values[key] = value

    def set_if_absent(self, key: str, value: str, **metadata: Any) -> bool:
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def compare_and_set(self, key: str, expected: str, value: str, **metadata: Any) -> bool:
        if self.values.get(key) != expected:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


def idp_wire(idp_id: str, kind: str, config: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    """One engine ``IdpConfig`` as ``idp.list`` answers it."""
    record = {
        "idp_id": idp_id,
        "kind": kind,
        "display_name": extra.pop("display_name", idp_id),
        "enabled": extra.pop("enabled", True),
        "config_json": json.dumps(config),
        "jit_policy": extra.pop("jit_policy", "deny"),
        "email_domains": extra.pop("email_domains", []),
        "order": extra.pop("order", 0),
        "rules": extra.pop("rules", []),
    }
    record.update(extra)
    return record


class FakeIdentityPort:
    """Records ops; answers ``idp.list`` from ``idps`` and every other op from
    ``handlers[(family, op)]`` (default: an ``ok`` sign-in or ``done``)."""

    def __init__(self, idps: list[dict[str, Any]] | None = None) -> None:
        self.idps = idps or []
        self.calls: list[dict[str, Any]] = []
        self.handlers: dict[tuple[str, str], Handler] = {}

    def ops(self, family: str, op: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["family"] == family and c["op"] == op]

    def refuse(self, family: str, op: str, code: str) -> None:
        def handler(_: Mapping[str, Any]) -> Mapping[str, Any]:
            raise IdentityRefused(code)

        self.handlers[(family, op)] = handler

    async def call(self, op: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(json.loads(json.dumps(op)))
        key = (str(op["family"]), str(op["op"]))
        if key == ("idp", "list"):
            return {"kind": "idps", "value": self.idps}
        handler = self.handlers.get(key)
        if handler is not None:
            return handler(op)
        if key == ("credential", "external_login"):
            principal = f"usr:{op['request']['subject']}"
            return {"kind": "authenticate", "value": {"outcome": "ok", "principal_id": principal}}
        return {"kind": "done", "value": {"changed": True}}
