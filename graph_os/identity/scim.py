"""SCIM 2.0 provisioning server (IDM-14).

``/scim/v2/{Users,Groups,ServiceProviderConfig,Schemas,ResourceTypes}``.

**Who may call.** The bearer is a GraphOS API key of a service account that
holds exactly ``identity:provision`` (never implied by ``identity:admin`` or
``*``). The key's principal is bound to ONE ``kind=scim`` IdP: the IdP whose
``config_json.provisioner`` names it. Every engine op is sent under that
principal's own authority, and the engine enforces the same binding
(``CONTRACT-REQUEST.md`` A2), so a leaked key can only provision its own IdP's
users and can never grant a role: roles come only from the IdP's mapping
rules, which an administrator installs.

**Resources.** A SCIM ``User`` is one principal the IdP provisioned: ``id`` is
the principal id, ``externalId`` the link subject. A ``Group`` is a directory
group of that IdP; its members feed the ``groups`` claim path of the IdP's
mapping rules.

**Deprovisioning.** ``active=false`` and ``DELETE`` both deprovision: sessions
and API keys are revoked, the principal, its links and every owned datum are
kept. A deprovisioned user still answers ``GET`` with ``active: false``.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from graph_os.identity.idp_common import (
    IdentityPort,
    IdentityRefused,
    IdpDirectory,
    IdpRecord,
    call_expect,
    identity_op,
)
from graph_os.identity.scim_schema import (
    MAX_RESULTS,
    ScimError,
    ScimGroup,
    ScimUser,
    apply_group_patch,
    apply_user_patch,
    error_body,
    list_body,
    parse_filter,
    resource_types,
    schemas_body,
    service_provider_config,
)

__all__ = [
    "PROVISION_SCOPE",
    "SCIM_BASE",
    "ApiKeyProvisionerAuth",
    "Provisioner",
    "ProvisionerAuth",
    "ScimServer",
]

SCIM_BASE = "/scim/v2"
PROVISION_SCOPE = "identity:provision"
_MEDIA = "application/scim+json"
_GENERATED_SUBJECT = "scim-gen:"
_PAGE = 500
_MAX_BODY = 1_048_576
_REFUSALS: dict[str, tuple[int, str, str | None]] = {
    "collision": (409, "userName or id already exists", "uniqueness"),
    "not_found": (404, "no such resource", None),
    "not_authorized": (403, "this key may not provision this resource", None),
}


@dataclass(frozen=True)
class Provisioner:
    """An authenticated SCIM caller: its principal and a port acting as it."""

    principal_id: str
    port: IdentityPort


class ProvisionerAuth(Protocol):
    async def authenticate(self, authorization: str | None) -> Provisioner | None:
        """The caller behind an ``Authorization`` header, or ``None``."""
        ...


class ApiKeyProvisionerAuth:
    """Bearer API key → the key's CURRENT authority (identity-graphos
    ``IdentityBroker.verify_api_key``); only an active service principal
    whose scopes contain ``identity:provision`` is admitted."""

    def __init__(
        self,
        verify: Callable[[str], Awaitable[Any]],
        port_for: Callable[[Any], IdentityPort],
    ) -> None:
        self._verify = verify
        self._port_for = port_for

    async def authenticate(self, authorization: str | None) -> Provisioner | None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            return None
        resolution = await self._verify(token.strip())
        if resolution is None or getattr(resolution, "status", None) != "active":
            return None
        if getattr(resolution, "kind", None) != "service":
            return None
        if PROVISION_SCOPE not in getattr(resolution, "scopes", ()):
            return None
        return Provisioner(str(resolution.principal_id), self._port_for(resolution))


def _scim(body: Any, status: int = 200, location: str | None = None) -> Response:
    headers = {"Location": location} if location else None
    return JSONResponse(body, status_code=status, media_type=_MEDIA, headers=headers)


def _error(error: ScimError) -> Response:
    return _scim(error_body(error), error.status)


def _refusal(refusal: IdentityRefused) -> ScimError:
    status, detail, scim_type = _REFUSALS.get(
        refusal.code, (400, "the identity store refused the change", None)
    )
    return ScimError(status, detail, scim_type)


async def _json_body(request: Request) -> Mapping[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        raise ScimError(413, "request body too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise ScimError(400, "body is not JSON", "invalidSyntax") from None
    if not isinstance(body, Mapping):
        raise ScimError(400, "body must be a JSON object", "invalidSyntax")
    return body


def _paging(request: Request) -> tuple[int, int]:
    try:
        start = max(1, int(request.query_params.get("startIndex", "1")))
        count = int(request.query_params.get("count", str(MAX_RESULTS)))
    except ValueError:
        raise ScimError(
            400, "startIndex and count must be integers", "invalidValue"
        ) from None
    return start, min(max(count, 0), MAX_RESULTS)


@dataclass(frozen=True)
class _Call:
    """One authenticated SCIM request: its IdP and the provisioner's port."""

    idp_id: str
    port: IdentityPort
    base: str

    async def ask(self, op: str, request: Mapping[str, Any], kind: str) -> Any:
        try:
            return await call_expect(self.port, identity_op("idp", op, request), kind)
        except IdentityRefused as refusal:
            raise _refusal(refusal) from None

    async def collect(
        self, op: str, kind: str, query: Mapping[str, Any], key: Callable[[Any], str]
    ) -> list[Any]:
        rows: list[Any] = []
        after: str | None = None
        while True:
            page_query = {"idp_id": self.idp_id, "limit": _PAGE, **query}
            page = await self.ask(
                op, page_query | ({"after": after} if after else {}), kind
            )
            rows.extend(page)
            if len(page) < _PAGE:
                return rows
            after = key(page[-1])

    def user_location(self, principal_id: str = "") -> str:
        return f"{self.base}/Users/{principal_id}".rstrip("/")

    def group_location(self, group_id: str) -> str:
        return f"{self.base}/Groups/{group_id}"


def _external_id(subject: str) -> str | None:
    return None if subject.startswith(_GENERATED_SUBJECT) else subject


def _user_resource(call: _Call, row: Mapping[str, Any]) -> dict[str, Any]:
    subject = str(row["subject"])
    principal = str(row["user"]["principal_id"])
    user = ScimUser.from_engine(row, _external_id(subject))
    return user.resource(principal, call.user_location(principal))


def _group_resource(call: _Call, row: Mapping[str, Any]) -> dict[str, Any]:
    group_id = str(row["group_id"])
    group = ScimGroup.from_engine(row)
    return group.resource(group_id, call.group_location(group_id), call.user_location())


def _principal_of(row: Any) -> str:
    return str(row["user"]["principal_id"])


def _group_of(row: Any) -> str:
    return str(row["group_id"])


class ScimServer:
    """The SCIM endpoints over the engine's provisioning ops."""

    def __init__(self, *, auth: ProvisionerAuth, directory: IdpDirectory) -> None:
        self._auth = auth
        self._directory = directory

    async def _bound_idp(self, principal_id: str) -> IdpRecord | None:
        bound = [
            record
            for record in await self._directory.records()
            if record.kind == "scim"
            and record.enabled
            and record.config.get("provisioner") == principal_id
        ]
        return bound[0] if len(bound) == 1 else None

    async def _call(self, request: Request) -> _Call:
        provisioner = await self._auth.authenticate(
            request.headers.get("authorization")
        )
        if provisioner is None:
            raise ScimError(401, "a provisioning API key is required")
        record = await self._bound_idp(provisioner.principal_id)
        if record is None:
            raise ScimError(
                403, "this key is not bound to exactly one enabled SCIM IdP"
            )
        base = str(request.base_url).rstrip("/") + SCIM_BASE
        return _Call(record.idp_id, provisioner.port, base)

    # -- users -----------------------------------------------------------
    async def _user_row(self, call: _Call, principal_id: str) -> Mapping[str, Any]:
        rows = await call.ask(
            "list_provisioned",
            {"idp_id": call.idp_id, "limit": 1, "principal_id": principal_id},
            "provisioned",
        )
        if not rows:
            raise ScimError(404, "no such user")
        row: Mapping[str, Any] = rows[0]
        return row

    async def _save_user(self, call: _Call, user: ScimUser, subject: str) -> Response:
        view = await call.ask("provision", user.provision(call.idp_id, subject), "user")
        row = {"subject": subject, "user": view}
        return _scim(_user_resource(call, row))

    async def _create_user(self, call: _Call, request: Request) -> Response:
        user = ScimUser.parse(await _json_body(request))
        queries = [{"username": user.user_name}]
        if user.external_id:
            queries.append({"subject": user.external_id})
        for query in queries:
            if await call.ask(
                "list_provisioned",
                {"idp_id": call.idp_id, "limit": 1, **query},
                "provisioned",
            ):
                raise ScimError(409, "the user already exists", "uniqueness")
        subject = user.external_id or f"{_GENERATED_SUBJECT}{secrets.token_hex(16)}"
        view = await call.ask("provision", user.provision(call.idp_id, subject), "user")
        resource = _user_resource(call, {"subject": subject, "user": view})
        return _scim(resource, 201, resource["meta"]["location"])

    async def _list_users(self, call: _Call, request: Request) -> Response:
        wanted = parse_filter(request.query_params.get("filter"), "User")
        query = {wanted.field: wanted.value} if wanted else {}
        rows = await call.collect(
            "list_provisioned", "provisioned", query, _principal_of
        )
        return self._page(request, [_user_resource(call, row) for row in rows])

    async def _replace_user(
        self, call: _Call, request: Request, principal_id: str
    ) -> Response:
        row = await self._user_row(call, principal_id)
        user = ScimUser.parse(await _json_body(request))
        return await self._save_user(call, user, str(row["subject"]))

    async def _patch_user(
        self, call: _Call, request: Request, principal_id: str
    ) -> Response:
        row = await self._user_row(call, principal_id)
        subject = str(row["subject"])
        current = ScimUser.from_engine(row, _external_id(subject))
        patched = apply_user_patch(current, await _json_body(request))
        return await self._save_user(call, patched, subject)

    async def _delete_user(self, call: _Call, principal_id: str) -> Response:
        row = await self._user_row(call, principal_id)
        subject = str(row["subject"])
        current = ScimUser.from_engine(row, _external_id(subject))
        await call.ask(
            "provision",
            {**current.provision(call.idp_id, subject), "active": False},
            "user",
        )
        return Response(status_code=204)

    # -- groups ----------------------------------------------------------
    async def _group_row(self, call: _Call, group_id: str) -> Mapping[str, Any]:
        rows = await call.ask(
            "list_directory_groups",
            {"idp_id": call.idp_id, "limit": 1, "group_id": group_id},
            "directory_groups",
        )
        if not rows:
            raise ScimError(404, "no such group")
        row: Mapping[str, Any] = rows[0]
        return row

    async def _save_group(
        self, call: _Call, group: ScimGroup, group_id: str, status: int = 200
    ) -> Response:
        row = await call.ask(
            "provision_group", group.provision(call.idp_id, group_id), "directory_group"
        )
        resource = _group_resource(call, row)
        location = resource["meta"]["location"] if status == 201 else None
        return _scim(resource, status, location)

    async def _create_group(self, call: _Call, request: Request) -> Response:
        group = ScimGroup.parse(await _json_body(request))
        clash = {"idp_id": call.idp_id, "limit": 1, "display_name": group.display_name}
        if await call.ask("list_directory_groups", clash, "directory_groups"):
            raise ScimError(409, "a group with this displayName exists", "uniqueness")
        return await self._save_group(call, group, secrets.token_hex(16), 201)

    async def _list_groups(self, call: _Call, request: Request) -> Response:
        wanted = parse_filter(request.query_params.get("filter"), "Group")
        query = {wanted.field: wanted.value} if wanted else {}
        rows = await call.collect(
            "list_directory_groups", "directory_groups", query, _group_of
        )
        return self._page(request, [_group_resource(call, row) for row in rows])

    async def _replace_group(
        self, call: _Call, request: Request, group_id: str
    ) -> Response:
        await self._group_row(call, group_id)
        return await self._save_group(
            call, ScimGroup.parse(await _json_body(request)), group_id
        )

    async def _patch_group(
        self, call: _Call, request: Request, group_id: str
    ) -> Response:
        current = ScimGroup.from_engine(await self._group_row(call, group_id))
        patched = apply_group_patch(current, await _json_body(request))
        return await self._save_group(call, patched, group_id)

    async def _delete_group(self, call: _Call, group_id: str) -> Response:
        await call.ask(
            "remove_directory_group",
            {"idp_id": call.idp_id, "group_id": group_id},
            "done",
        )
        return Response(status_code=204)

    # -- dispatch --------------------------------------------------------
    @staticmethod
    def _page(request: Request, resources: list[dict[str, Any]]) -> Response:
        start, count = _paging(request)
        window = resources[start - 1 : start - 1 + count]
        return _scim(list_body(window, len(resources), start))

    async def _serve(
        self, request: Request, handler: Callable[[_Call], Awaitable[Response]]
    ) -> Response:
        try:
            return await handler(await self._call(request))
        except ScimError as error:
            return _error(error)

    async def users(self, request: Request) -> Response:
        """``GET`` (list/filter) and ``POST`` (create) on ``/Users``."""
        if request.method == "POST":
            return await self._serve(
                request, lambda call: self._create_user(call, request)
            )
        return await self._serve(request, lambda call: self._list_users(call, request))

    async def user(self, request: Request) -> Response:
        """``GET``/``PUT``/``PATCH``/``DELETE`` on ``/Users/{id}``."""
        principal_id = str(request.path_params["resource_id"])
        handlers: dict[str, Callable[[_Call], Awaitable[Response]]] = {
            "GET": lambda call: self._get_user(call, principal_id),
            "PUT": lambda call: self._replace_user(call, request, principal_id),
            "PATCH": lambda call: self._patch_user(call, request, principal_id),
            "DELETE": lambda call: self._delete_user(call, principal_id),
        }
        return await self._serve(request, handlers[request.method])

    async def _get_user(self, call: _Call, principal_id: str) -> Response:
        return _scim(_user_resource(call, await self._user_row(call, principal_id)))

    async def groups(self, request: Request) -> Response:
        """``GET`` (list/filter) and ``POST`` (create) on ``/Groups``."""
        if request.method == "POST":
            return await self._serve(
                request, lambda call: self._create_group(call, request)
            )
        return await self._serve(request, lambda call: self._list_groups(call, request))

    async def group(self, request: Request) -> Response:
        """``GET``/``PUT``/``PATCH``/``DELETE`` on ``/Groups/{id}``."""
        group_id = str(request.path_params["resource_id"])
        handlers: dict[str, Callable[[_Call], Awaitable[Response]]] = {
            "GET": lambda call: self._get_group(call, group_id),
            "PUT": lambda call: self._replace_group(call, request, group_id),
            "PATCH": lambda call: self._patch_group(call, request, group_id),
            "DELETE": lambda call: self._delete_group(call, group_id),
        }
        return await self._serve(request, handlers[request.method])

    async def _get_group(self, call: _Call, group_id: str) -> Response:
        return _scim(_group_resource(call, await self._group_row(call, group_id)))

    async def discovery(self, request: Request) -> Response:
        """``ServiceProviderConfig``, ``ResourceTypes`` and ``Schemas``."""
        name = request.url.path.rsplit("/", 1)[-1]

        async def answer(call: _Call) -> Response:
            if name == "ServiceProviderConfig":
                return _scim(service_provider_config())
            items = (
                resource_types(call.base) if name == "ResourceTypes" else schemas_body()
            )
            return _scim(list_body(items, len(items), 1))

        return await self._serve(request, answer)

    def routes(self) -> list[Route]:
        item = ["GET", "PUT", "PATCH", "DELETE"]
        return [
            Route(f"{SCIM_BASE}/Users", self.users, methods=["GET", "POST"]),
            Route(f"{SCIM_BASE}/Users/{{resource_id}}", self.user, methods=item),
            Route(f"{SCIM_BASE}/Groups", self.groups, methods=["GET", "POST"]),
            Route(f"{SCIM_BASE}/Groups/{{resource_id}}", self.group, methods=item),
            *(
                Route(f"{SCIM_BASE}/{name}", self.discovery, methods=["GET"])
                for name in ("ServiceProviderConfig", "ResourceTypes", "Schemas")
            ),
        ]
