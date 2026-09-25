"""SCIM 2.0 resources, filters and PATCH (RFC 7643 / RFC 7644), protocol only.

Pure functions between SCIM JSON and the engine's provisioning shapes
(``CONTRACT-REQUEST.md`` A2-A4); :mod:`.scim` serves them. Supported subset:

* ``User``: ``userName``, ``externalId``, ``displayName``, ``name``
  (``formatted``/``givenName``/``familyName``), ``emails`` (the primary or
  first value), ``active``;
* ``Group``: ``displayName``, ``externalId``, ``members`` (user ids);
* filters: one ``<attribute> eq "<value>"`` on ``id``, ``externalId``,
  ``userName`` (users) or ``displayName`` (groups) -- anything else is
  ``invalidFilter``;
* PATCH ``add`` / ``replace`` / ``remove`` (any case, as Entra ID sends them),
  with or without a ``path``, including ``members[value eq "<id>"]``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "ERROR_SCHEMA",
    "GROUP_SCHEMA",
    "LIST_SCHEMA",
    "PATCH_SCHEMA",
    "USER_SCHEMA",
    "ScimError",
    "ScimFilter",
    "ScimGroup",
    "ScimUser",
    "apply_group_patch",
    "apply_user_patch",
    "error_body",
    "list_body",
    "parse_filter",
    "resource_types",
    "schemas_body",
    "service_provider_config",
]

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
_SPC_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
_RT_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
_SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
MAX_RESULTS = 500
_MAX_TEXT = 256
_MAX_MEMBERS = 10_000


class ScimError(ValueError):
    """An RFC 7644 §3.12 error: HTTP ``status`` and an optional ``scim_type``."""

    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type


def error_body(error: ScimError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [ERROR_SCHEMA],
        "status": str(error.status),
        "detail": error.detail,
    }
    if error.scim_type:
        body["scimType"] = error.scim_type
    return body


def _invalid(detail: str) -> ScimError:
    return ScimError(400, detail, "invalidValue")


def _text(value: Any, name: str, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise _invalid(f"{name} is required")
        return None
    if not isinstance(value, str) or len(value) > _MAX_TEXT:
        raise _invalid(f"{name} must be a string of at most {_MAX_TEXT} characters")
    return value.strip() or None


def _bool(value: Any) -> bool:
    """SCIM booleans, including Entra ID's ``"True"`` / ``"False"`` strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in ("true", "false"):
        return value.casefold() == "true"
    raise _invalid("active must be a boolean")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _primary_email(emails: Any) -> str | None:
    if emails in (None, []):
        return None
    if not isinstance(emails, list) or not all(isinstance(e, Mapping) for e in emails):
        raise _invalid("emails must be a list of objects")
    chosen = next((e for e in emails if e.get("primary") in (True, "true", "True")), emails[0])
    return _text(chosen.get("value"), "emails.value")


def _name(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _invalid("name must be an object")
    parts = {k: _text(value.get(k), f"name.{k}") for k in ("formatted", "givenName", "familyName")}
    return {k: v for k, v in parts.items() if v}


@dataclass(frozen=True)
class ScimUser:
    """The SCIM user attributes the engine keeps."""

    user_name: str
    external_id: str | None = None
    display_name: str | None = None
    name: Mapping[str, str] = field(default_factory=dict)
    email: str | None = None
    active: bool = True

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> ScimUser:
        user_name = _text(body.get("userName"), "userName", required=True)
        return cls(
            user_name=str(user_name),
            external_id=_text(body.get("externalId"), "externalId"),
            display_name=_text(body.get("displayName"), "displayName"),
            name=_name(body.get("name")),
            email=_primary_email(body.get("emails")),
            active=_bool(body.get("active", True)),
        )

    def effective_display_name(self) -> str | None:
        joined = " ".join(p for p in (self.name.get("givenName"), self.name.get("familyName")) if p)
        return self.display_name or self.name.get("formatted") or joined or None

    def provision(self, idp_id: str, subject: str) -> dict[str, Any]:
        """The engine ``idp.provision`` body (``CONTRACT-REQUEST.md`` A2)."""
        request: dict[str, Any] = {
            "idp_id": idp_id,
            "subject": subject,
            "username": self.user_name,
            "active": self.active,
            "claims": {"email": [self.email]} if self.email else {},
        }
        display = self.effective_display_name()
        if display:
            request["display_name"] = display
        if self.email:
            request["email"] = self.email
        return request

    @classmethod
    def from_engine(cls, row: Mapping[str, Any], external_id: str | None) -> ScimUser:
        user = row["user"]
        status = str(user.get("status"))
        return cls(
            user_name=str(user["username"]),
            external_id=external_id,
            display_name=user.get("display_name"),
            email=user.get("email"),
            active=status not in ("deprovisioned", "disabled"),
        )

    def resource(self, principal_id: str, location: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schemas": [USER_SCHEMA],
            "id": principal_id,
            "userName": self.user_name,
            "active": self.active,
            "meta": {"resourceType": "User", "location": location},
        }
        optional = {
            "externalId": self.external_id,
            "displayName": self.effective_display_name(),
            "name": dict(self.name) or None,
            "emails": [{"value": self.email, "primary": True, "type": "work"}] if self.email else None,
        }
        body.update({k: v for k, v in optional.items() if v})
        return body


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
def _members(value: Any) -> frozenset[str]:
    if value in (None, []):
        return frozenset()
    if not isinstance(value, list) or len(value) > _MAX_MEMBERS:
        raise _invalid("members must be a list")
    ids = [m.get("value") if isinstance(m, Mapping) else None for m in value]
    if not all(isinstance(i, str) and 0 < len(i) <= _MAX_TEXT for i in ids):
        raise _invalid("every member needs a user id value")
    return frozenset(str(i) for i in ids)


@dataclass(frozen=True)
class ScimGroup:
    """A directory group: a name and its member user ids."""

    display_name: str
    external_id: str | None = None
    members: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> ScimGroup:
        display = _text(body.get("displayName"), "displayName", required=True)
        return cls(
            display_name=str(display),
            external_id=_text(body.get("externalId"), "externalId"),
            members=_members(body.get("members")),
        )

    @classmethod
    def from_engine(cls, row: Mapping[str, Any]) -> ScimGroup:
        return cls(
            display_name=str(row["display_name"]),
            external_id=row.get("external_id"),
            members=frozenset(str(m) for m in row.get("members", ())),
        )

    def provision(self, idp_id: str, group_id: str) -> dict[str, Any]:
        """The engine ``idp.provision_group`` body (``CONTRACT-REQUEST.md`` A4)."""
        request: dict[str, Any] = {
            "idp_id": idp_id,
            "group_id": group_id,
            "display_name": self.display_name,
            "members": sorted(self.members),
        }
        if self.external_id:
            request["external_id"] = self.external_id
        return request

    def resource(self, group_id: str, location: str, user_location: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schemas": [GROUP_SCHEMA],
            "id": group_id,
            "displayName": self.display_name,
            "members": [
                {"value": member, "$ref": f"{user_location}/{member}"}
                for member in sorted(self.members)
            ],
            "meta": {"resourceType": "Group", "location": location},
        }
        if self.external_id:
            body["externalId"] = self.external_id
        return body


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
_FILTER = re.compile(r'^\s*([A-Za-z][A-Za-z0-9.]*)\s+eq\s+"((?:[^"\\]|\\.){1,256})"\s*$', re.I)
_USER_FILTERS = {"id": "principal_id", "externalid": "subject", "username": "username"}
_GROUP_FILTERS = {"id": "group_id", "externalid": "external_id", "displayname": "display_name"}


@dataclass(frozen=True)
class ScimFilter:
    """One equality filter, as the engine query field it becomes."""

    field: str
    value: str


def parse_filter(text: str | None, resource: str) -> ScimFilter | None:
    """``None`` for no filter; :class:`ScimError` ``invalidFilter`` otherwise."""
    if text is None or not text.strip():
        return None
    match = _FILTER.match(text)
    allowed = _USER_FILTERS if resource == "User" else _GROUP_FILTERS
    target = allowed.get(match.group(1).casefold()) if match else None
    if match is None or target is None:
        raise ScimError(400, "only one `<attribute> eq \"<value>\"` filter is supported", "invalidFilter")
    value = re.sub(r"\\(.)", r"\1", match.group(2))
    return ScimFilter(target, value)


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------
_MEMBER_PATH = re.compile(r'^members\[value eq "([^"]{1,256})"\]$', re.I)


def _operations(body: Mapping[str, Any]) -> list[tuple[str, str | None, Any]]:
    if PATCH_SCHEMA not in (body.get("schemas") or ()):
        raise _invalid("a PATCH body must declare the PatchOp schema")
    raw = body.get("Operations")
    if not isinstance(raw, list) or not raw or len(raw) > 100:
        raise _invalid("Operations must be a non-empty list")
    ops: list[tuple[str, str | None, Any]] = []
    for item in raw:
        op = str(item.get("op", "")).casefold() if isinstance(item, Mapping) else ""
        if op not in ("add", "replace", "remove"):
            raise _invalid("op must be add, replace or remove")
        path = item.get("path")
        ops.append((op, str(path) if path else None, item.get("value")))
    return ops


_USER_PATHS = {
    "active": "active",
    "username": "user_name",
    "displayname": "display_name",
    "name.formatted": "formatted",
    "name.givenname": "givenName",
    "name.familyname": "familyName",
    "emails": "email",
    'emails[type eq "work"].value': "email",
    "emails[primary eq true].value": "email",
}
_NAME_PARTS = frozenset({"formatted", "givenName", "familyName"})


def _user_value(target: str, value: Any) -> Any:
    if target == "active":
        return _bool(value)
    if target == "email" and isinstance(value, list):
        return _primary_email(value)
    return _text(value, target, required=target == "user_name")


def _set_user(user: ScimUser, target: str, value: Any) -> ScimUser:
    if target in _NAME_PARTS:
        name = {k: v for k, v in user.name.items() if k != target}
        return replace(user, name={**name, target: value} if value else name)
    return replace(user, **{target: value})


def _user_path_op(user: ScimUser, op: str, path: str, value: Any) -> ScimUser:
    target = _USER_PATHS.get(path.casefold())
    if target is None:
        raise ScimError(400, f"unsupported path {path!r}", "invalidPath")
    if op == "remove":
        if target in ("user_name", "active"):
            raise ScimError(400, f"{path} cannot be removed", "mutability")
        return _set_user(user, target, None)
    return _set_user(user, target, _user_value(target, value))


def _user_object_op(user: ScimUser, value: Any) -> ScimUser:
    if not isinstance(value, Mapping):
        raise _invalid("a PATCH without a path needs an object value")
    for key, item in value.items():
        if key == "name" and isinstance(item, Mapping):
            for part, text in item.items():
                user = _user_path_op(user, "replace", f"name.{part}", text)
        else:
            user = _user_path_op(user, "replace", str(key), item)
    return user


def apply_user_patch(user: ScimUser, body: Mapping[str, Any]) -> ScimUser:
    """Apply an RFC 7644 PatchOp to a user."""
    for op, path, value in _operations(body):
        if path is None and op != "remove":
            user = _user_object_op(user, value)
        elif path is None:
            raise ScimError(400, "remove needs a path", "noTarget")
        else:
            user = _user_path_op(user, op, path, value)
    return user


def _group_members_op(group: ScimGroup, op: str, value: Any) -> ScimGroup:
    ids = _members(value)
    if op == "add":
        return replace(group, members=group.members | ids)
    if op == "remove":
        return replace(group, members=group.members - ids if ids else frozenset())
    return replace(group, members=ids)


def _group_path_op(group: ScimGroup, op: str, path: str, value: Any) -> ScimGroup:
    member = _MEMBER_PATH.match(path)
    if member is not None and op == "remove":
        return replace(group, members=group.members - {member.group(1)})
    lowered = path.casefold()
    if lowered == "members":
        return _group_members_op(group, op, value)
    if lowered == "displayname" and op != "remove":
        return replace(group, display_name=str(_text(value, "displayName", required=True)))
    if lowered == "externalid":
        return replace(group, external_id=None if op == "remove" else _text(value, "externalId"))
    raise ScimError(400, f"unsupported path {path!r}", "invalidPath")


def apply_group_patch(group: ScimGroup, body: Mapping[str, Any]) -> ScimGroup:
    """Apply an RFC 7644 PatchOp to a group."""
    for op, path, value in _operations(body):
        if path is not None:
            group = _group_path_op(group, op, path, value)
            continue
        if op == "remove" or not isinstance(value, Mapping):
            raise ScimError(400, "a PATCH without a path needs an object value", "noTarget")
        for key, item in value.items():
            group = _group_path_op(group, op, str(key), item)
    return group


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------
def list_body(resources: list[dict[str, Any]], total: int, start: int) -> dict[str, Any]:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def service_provider_config() -> dict[str, Any]:
    return {
        "schemas": [_SPC_SCHEMA],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_RESULTS},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "API key",
                "description": "A GraphOS API key holding exactly identity:provision",
            }
        ],
    }


def resource_types(base: str) -> list[dict[str, Any]]:
    return [
        {
            "schemas": [_RT_SCHEMA],
            "id": name,
            "name": name,
            "endpoint": f"/{name}s",
            "schema": schema,
            "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/{name}"},
        }
        for name, schema in (("User", USER_SCHEMA), ("Group", GROUP_SCHEMA))
    ]


def _attribute(name: str, kind: str = "string", **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": kind, "multiValued": False, "required": False, **extra}


def schemas_body() -> list[dict[str, Any]]:
    user = [
        _attribute("userName", required=True, uniqueness="server"),
        _attribute("displayName"),
        _attribute("name", "complex"),
        _attribute("emails", "complex", multiValued=True),
        _attribute("active", "boolean"),
    ]
    group = [
        _attribute("displayName", required=True),
        _attribute("members", "complex", multiValued=True),
    ]
    return [
        {"schemas": [_SCHEMA_SCHEMA], "id": USER_SCHEMA, "name": "User", "attributes": user},
        {"schemas": [_SCHEMA_SCHEMA], "id": GROUP_SCHEMA, "name": "Group", "attributes": group},
    ]
