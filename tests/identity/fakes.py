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

    def compare_and_set(
        self, key: str, expected: str, value: str, **metadata: Any
    ) -> bool:
        if self.values.get(key) != expected:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


def idp_wire(
    idp_id: str, kind: str, config: Mapping[str, Any], **extra: Any
) -> dict[str, Any]:
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
            return {
                "kind": "authenticate",
                "value": {"outcome": "ok", "principal_id": principal},
            }
        return {"kind": "done", "value": {"changed": True}}


class ProvisioningEngine:
    """The engine side of ``idp.provision`` / ``list_provisioned`` and the
    directory-group ops (``CONTRACT-REQUEST.md`` A2-A4), in memory, including
    the provisioner binding: a port acts as one principal, and only the SCIM
    IdP whose ``config.provisioner`` names it answers."""

    def __init__(self, idps: list[dict[str, Any]]) -> None:
        self.idps = idps
        self.users: dict[str, dict[str, Any]] = {}
        self.links: dict[tuple[str, str], str] = {}
        self.groups: dict[tuple[str, str], dict[str, Any]] = {}
        self.revoked: list[str] = []
        self.claims: dict[str, dict[str, list[str]]] = {}

    def port_for(self, principal: str) -> FakeIdentityPort:
        port = FakeIdentityPort(self.idps)
        for op, handler in (
            ("provision", self._provision),
            ("list_provisioned", self._list_provisioned),
            ("provision_group", self._provision_group),
            ("remove_directory_group", self._remove_group),
            ("list_directory_groups", self._list_groups),
        ):
            port.handlers[("idp", op)] = self._bound(principal, handler)
        return port

    def _bound(self, principal: str, handler: Handler) -> Handler:
        def guarded(op: Mapping[str, Any]) -> Mapping[str, Any]:
            idp_id = op["request"]["idp_id"]
            idp = next((i for i in self.idps if i["idp_id"] == idp_id), None)
            if (
                idp is None
                or json.loads(idp["config_json"]).get("provisioner") != principal
            ):
                raise IdentityRefused("IDENTITY_NOT_AUTHORIZED")
            return handler(op["request"])

        return guarded

    def _view(self, principal: str) -> dict[str, Any]:
        return dict(self.users[principal])

    def _provision(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        key = (request["idp_id"], request["subject"])
        principal = self.links.get(key)
        taken = {u["username"]: p for p, u in self.users.items()}
        if taken.get(request["username"], principal) != principal:
            raise IdentityRefused("IDENTITY_COLLISION")
        if principal is None:
            if not request["active"]:
                return {"kind": "done", "value": {"changed": False}}
            principal = f"usr:{len(self.users) + 1:04d}"
            self.links[key] = principal
            self.users[principal] = {
                "principal_id": principal,
                "source": f"scim:{key[0]}",
            }
        user = self.users[principal]
        user.update(
            username=request["username"], display_name=request.get("display_name")
        )
        user.update(
            email=request.get("email"),
            status="active" if request["active"] else "deprovisioned",
        )
        if not request["active"]:
            self.revoked.append(principal)
        self.claims[principal] = dict(request.get("claims", {}))
        return {"kind": "user", "value": self._view(principal)}

    def _list_provisioned(self, query: Mapping[str, Any]) -> Mapping[str, Any]:
        links = [(s, p) for (i, s), p in self.links.items() if i == query["idp_id"]]
        rows = [
            {"subject": s, "user": self._view(p)}
            for s, p in sorted(links, key=lambda sp: sp[1])
        ]
        rows = [r for r in rows if _row_matches(r, query)]
        return {
            "kind": "provisioned",
            "value": _page(rows, query, lambda r: r["user"]["principal_id"]),
        }

    def _provision_group(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        linked = {p for (i, _), p in self.links.items() if i == request["idp_id"]}
        if not set(request["members"]) <= linked:
            raise IdentityRefused("IDENTITY_NOT_FOUND")
        self.groups[(request["idp_id"], request["group_id"])] = dict(request)
        return {"kind": "directory_group", "value": dict(request)}

    def _remove_group(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        removed = self.groups.pop((request["idp_id"], request["group_id"]), None)
        return {"kind": "done", "value": {"changed": removed is not None}}

    def _list_groups(self, query: Mapping[str, Any]) -> Mapping[str, Any]:
        rows = [g for (i, _), g in sorted(self.groups.items()) if i == query["idp_id"]]
        wanted = {
            k: query[k]
            for k in ("group_id", "display_name", "external_id")
            if query.get(k)
        }
        rows = [g for g in rows if all(g.get(k) == v for k, v in wanted.items())]
        return {
            "kind": "directory_groups",
            "value": _page(rows, query, lambda g: g["group_id"]),
        }


def _row_matches(row: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    fields = {"subject": row["subject"], **row["user"]}
    wanted = {
        k: query[k] for k in ("subject", "principal_id", "username") if query.get(k)
    }
    return all(fields.get(k) == v for k, v in wanted.items())


def _page(
    rows: list[Any], query: Mapping[str, Any], key: Callable[[Any], str]
) -> list[Any]:
    after = query.get("after")
    return [r for r in rows if not after or key(r) > after][: query["limit"]]
