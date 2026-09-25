"""Seams every external identity authority shares (IDM-12..15, IDM-19).

The design (``plans/refactor/architecture/IDENTITY-AND-AUTH-MODES-DESIGN.md``
§3-§4) keeps ONE authority: the engine's identity store. An authority module
here only proves a protocol assertion (an OIDC ID token, a SAML response, an
LDAP bind) and forwards ``(idp_id, subject, claims)`` to the engine's
``credential.external_login`` op. The engine then resolves the link or applies
the IdP's JIT policy, recomputes the IdP-sourced roles and group memberships
from the mapping rules, and opens a server-side session under a token GraphOS
generated. Nothing in this package decides a role.

Contents:

* :class:`IdentityPort` -- the one call into ``Method::Identity``; the wire
  shape is ``{"family": ..., "op": ..., "request": {...}}`` and every reply is
  ``{"kind": ..., "value": ...}``.
* :func:`flatten_claims` -- a protocol's claim document to the
  ``claim_path -> [values]`` map the mapping rules read.
* :class:`OneShotStore` -- single-use transaction and replay records over the
  durable secrets backend's atomic ``set_if_absent`` / ``compare_and_set``, so
  GraphOS replicas stay stateless.
* :class:`EngineLoginCompleter` -- the verified assertion to a session cookie.
* :func:`dry_run` -- the admin preview of what a sample assertion would map to.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

__all__ = [
    "SESSION_COOKIE",
    "DryRunResult",
    "EngineLoginCompleter",
    "ExternalAssertion",
    "IdentityPort",
    "IdentityRefused",
    "IdpDirectory",
    "IdpRecord",
    "LoginCompleter",
    "OneShotBackend",
    "OneShotStore",
    "SecretResolver",
    "UnknownIdp",
    "call_expect",
    "dry_run",
    "flatten_claims",
    "identity_op",
    "login_error",
    "login_options",
    "new_session_token",
    "refusal_code",
    "require_https",
    "truncate_ip",
]

SESSION_COOKIE = "__Host-graphos_session"
"""Design §3.3: the opaque 256-bit session id; only its hash reaches the store."""

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_SESSION_TOKEN_BYTES = 32
_TOMBSTONE = "__consumed__"
_MAX_CLAIM_VALUES = 256
_MAX_CLAIM_VALUE_CHARS = 256


class IdentityRefused(RuntimeError):
    """The engine refused an identity op or answered an unexpected reply.

    A port raises it with the engine refusal's snake_case code as the first
    argument (``collision``, ``not_found``, ``not_authorized``, ...).
    """

    @property
    def code(self) -> str:
        return _normalise_code(str(self.args[0]) if self.args else "")


def refusal_code(refusal: BaseException) -> str:
    """The snake_case refusal code, whichever spelling the port raised.

    The engine's typed refusals travel as ``IDENTITY_COLLISION``; a port may
    also raise the bare ``collision``. Both normalise to ``collision``.
    """
    if isinstance(refusal, IdentityRefused):
        return refusal.code
    raw = getattr(refusal, "code", None) or (refusal.args[0] if refusal.args else "")
    return _normalise_code(str(raw))


def _normalise_code(raw: str) -> str:
    return raw.removeprefix("IDENTITY_").lower() or "refused"


class IdentityPort(Protocol):
    """One ``Method::Identity`` call. The implementation owns the envelope and
    the acting principal; callers only build the op."""

    async def call(self, op: Mapping[str, Any]) -> Mapping[str, Any]:
        """Send ``op`` and return the engine's ``IdentityReply``."""
        ...


class SecretResolver(Protocol):
    """Reads one secret by reference (AU ``SecretsClient.get``); a value is
    never stored in an IdP configuration, only its ``secret_ref``."""

    def get(self, key: str) -> str | None: ...


def identity_op(
    family: str, op: str, request: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build one ``IdentityOp`` in its tagged wire form."""
    body: dict[str, Any] = {"family": family, "op": op}
    if request is not None:
        body["request"] = dict(request)
    return body


async def call_expect(port: IdentityPort, op: Mapping[str, Any], kind: str) -> Any:
    """Call ``op`` and return the reply's value, refusing any other reply kind."""
    reply = await port.call(op)
    if reply.get("kind") != kind:
        raise IdentityRefused(f"identity op {op.get('op')!r} did not answer {kind!r}")
    return reply.get("value")


def require_https(value: str) -> str:
    """``value`` if it is an absolute https URL without a fragment."""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.fragment:
        raise ValueError("must be an absolute https URL without a fragment")
    return value


def new_session_token() -> str:
    """A caller-generated session id above the engine's entropy floor."""
    return secrets.token_urlsafe(_SESSION_TOKEN_BYTES)


def truncate_ip(address: str | None) -> str | None:
    """The audit/throttle prefix of a client address: /24 (v4), /48 (v6)."""
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    prefix = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------
def _walk(document: Mapping[str, Any], path: str) -> Any:
    node: Any = document
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _scalar(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        text = str(value)
        return text if 0 < len(text) <= _MAX_CLAIM_VALUE_CHARS else None
    return None


def _values(node: Any) -> list[str]:
    items = node if isinstance(node, list) else [node]
    scalars = (_scalar(item) for item in items[:_MAX_CLAIM_VALUES])
    return [value for value in scalars if value is not None]


def flatten_claims(
    document: Mapping[str, Any],
    paths: Iterable[str],
    *,
    group_paths: Iterable[str] = (),
) -> dict[str, list[str]]:
    """Project ``document`` onto ``paths`` as ``claim_path -> [values]``.

    Booleans become ``"true"``/``"false"`` (the engine links by verified
    e-mail only on the exact string ``"true"``); objects and floats are
    dropped; absent paths are omitted. A path in ``group_paths`` has one
    leading ``/`` removed from each value, so a Keycloak full-path group
    ``/elevation-approvers`` matches the engine's built-in group id.
    """
    stripped = set(group_paths)
    claims: dict[str, list[str]] = {}
    for path in dict.fromkeys(paths):
        values = _values(_walk(document, path))
        if path in stripped:
            values = [value.removeprefix("/") for value in values]
        if values:
            claims[path] = values
    return claims


@dataclass(frozen=True)
class ExternalAssertion:
    """A verified protocol assertion, reduced to what the engine reads."""

    idp_id: str
    subject: str
    claims: Mapping[str, list[str]]
    username_hint: str | None = None

    def login_request(
        self, session_token: str, ip_prefix: str | None
    ) -> dict[str, Any]:
        """The ``external_login`` request body."""
        body: dict[str, Any] = {
            "idp_id": self.idp_id,
            "subject": self.subject,
            "claims": {path: list(values) for path, values in self.claims.items()},
            "session_token": session_token,
        }
        if self.username_hint:
            body["username_hint"] = self.username_hint
        if ip_prefix:
            body["ip_prefix"] = ip_prefix
        return body


# ---------------------------------------------------------------------------
# Identity-provider directory
# ---------------------------------------------------------------------------
class UnknownIdp(LookupError):
    """No enabled identity provider of the requested kind has this id."""


@dataclass(frozen=True)
class IdpRecord:
    """One engine ``IdpConfig`` with its protocol configuration decoded."""

    idp_id: str
    kind: str
    display_name: str
    enabled: bool
    config: Mapping[str, Any]
    secret_ref: str | None
    email_domains: tuple[str, ...]
    order: int

    @classmethod
    def from_wire(cls, raw: Mapping[str, Any]) -> IdpRecord:
        config = json.loads(str(raw.get("config_json") or "{}"))
        return cls(
            idp_id=str(raw["idp_id"]),
            kind=str(raw["kind"]),
            display_name=str(raw.get("display_name", raw["idp_id"])),
            enabled=bool(raw.get("enabled")),
            config=config if isinstance(config, dict) else {},
            secret_ref=raw.get("secret_ref"),
            email_domains=tuple(
                str(d).casefold() for d in raw.get("email_domains", ())
            ),
            order=int(raw.get("order", 0)),
        )


class IdpDirectory:
    """The engine's IdP list (``idp.list``), cached for ``ttl_s`` seconds so a
    disabled or edited IdP takes effect on every replica within that bound."""

    def __init__(
        self, port: IdentityPort, *, ttl_s: float = 30.0, clock: Any = time.monotonic
    ) -> None:
        self._port = port
        self._ttl_s = ttl_s
        self._clock = clock
        self._cached: tuple[float, tuple[IdpRecord, ...]] | None = None

    async def records(self) -> tuple[IdpRecord, ...]:
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < self._ttl_s:
            return self._cached[1]
        raw = await call_expect(self._port, identity_op("idp", "list"), "idps")
        records = tuple(sorted((IdpRecord.from_wire(r) for r in raw), key=_idp_order))
        self._cached = (now, records)
        return records

    async def enabled(self, idp_id: str, kind: str) -> IdpRecord:
        for record in await self.records():
            if record.idp_id == idp_id and record.kind == kind and record.enabled:
                return record
        raise UnknownIdp(idp_id)


def _idp_order(record: IdpRecord) -> tuple[int, str]:
    return record.order, record.idp_id


_BROWSER_KINDS = frozenset({"oidc", "saml", "ldap"})


def login_options(
    records: Iterable[IdpRecord], email: str | None = None
) -> list[dict[str, str]]:
    """The sign-in choices a login page lists (enabled browser IdPs, in order).

    With an ``email`` whose domain an IdP claims (its ``email_domains`` hint),
    only that IdP is returned so the page can route straight to it.
    """
    usable = [r for r in records if _browser_usable(r)]
    domain = _email_domain(email)
    routed = [r for r in usable if domain and domain in r.email_domains]
    return [
        {"idp_id": r.idp_id, "kind": r.kind, "display_name": r.display_name}
        for r in (routed[:1] or usable)
    ]


def _browser_usable(record: IdpRecord) -> bool:
    return record.enabled and record.kind in _BROWSER_KINDS


def _email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.rpartition("@")[2].casefold()


# ---------------------------------------------------------------------------
# Single-use records
# ---------------------------------------------------------------------------
class OneShotBackend(Protocol):
    """The atomic subset of the secrets backend (AU ``SecretsClient``)."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, **metadata: Any) -> None: ...

    def set_if_absent(self, key: str, value: str, **metadata: Any) -> bool: ...

    def compare_and_set(
        self, key: str, expected: str, value: str, **metadata: Any
    ) -> bool: ...

    def delete(self, key: str) -> bool: ...


class OneShotStore:
    """Short-lived records that are read at most once, and ids seen at most once.

    ``take`` wins exactly once per key because it flips the stored bytes to a
    tombstone with ``compare_and_set`` before trusting them; ``first_sighting``
    is ``set_if_absent`` (atomic on the engine backend), reusing an entry only
    after it expired. Both survive a GraphOS restart and are shared by every
    replica.
    """

    def __init__(
        self, backend: OneShotBackend, namespace: str, *, clock: Any = time.time
    ) -> None:
        self._backend = backend
        self._namespace = namespace
        self._clock = clock

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def put(self, key: str, payload: Mapping[str, Any], ttl_s: float) -> None:
        record = {"exp": self._clock() + ttl_s, "payload": dict(payload)}
        self._backend.set(self._key(key), json.dumps(record), kind=self._namespace)

    def take(self, key: str) -> dict[str, Any] | None:
        """The payload stored under ``key`` if unexpired and not yet taken."""
        stored_key = self._key(key)
        raw = self._backend.get(stored_key)
        if raw is None or raw == _TOMBSTONE:
            return None
        if not self._backend.compare_and_set(stored_key, raw, _TOMBSTONE):
            return None
        self._backend.delete(stored_key)
        record = json.loads(raw)
        if float(record["exp"]) <= self._clock():
            return None
        payload: dict[str, Any] = record["payload"]
        return payload

    def first_sighting(self, key: str, ttl_s: float) -> bool:
        """``True`` exactly once per key within ``ttl_s``: the replay guard."""
        stored_key = self._key(key)
        marker = json.dumps({"exp": self._clock() + ttl_s})
        if self._backend.set_if_absent(stored_key, marker, kind=self._namespace):
            return True
        raw = self._backend.get(stored_key)
        if raw is None or float(json.loads(raw)["exp"]) > self._clock():
            return False
        return self._backend.compare_and_set(stored_key, raw, marker)


# ---------------------------------------------------------------------------
# Completing a sign-in
# ---------------------------------------------------------------------------
class LoginCompleter(Protocol):
    """Turns a verified assertion into the browser's next response."""

    async def complete(
        self, request: Request, assertion: ExternalAssertion
    ) -> Response: ...


_OUTCOME_ERRORS = {
    "bad": "denied",
    "throttled": "throttled",
    "mfa_enrollment_required": "mfa_enrollment",
    "password_change_required": "password_change",
}
"""Engine outcome -> the fixed error code the login page renders. Nothing from
the assertion or the engine's reply is ever reflected."""


LOGIN_PATH = "/auth/login"


def login_error(code: str) -> Response:
    """Back to the login page with a FIXED error code; nothing from the
    assertion, the directory or the engine is ever reflected."""
    return RedirectResponse(
        f"{LOGIN_PATH}?error={code}", status_code=303, headers=NO_STORE
    )


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


class EngineLoginCompleter:
    """``credential.external_login`` under a fresh session token; the cookie is
    set only when the engine opened a session (``ok`` or ``mfa_required``)."""

    def __init__(
        self,
        port: IdentityPort,
        *,
        landing_path: str = "/",
        mfa_path: str = "/auth/mfa",
        login_path: str = "/auth/login",
    ) -> None:
        self._port = port
        self._targets = {"ok": landing_path, "mfa_required": mfa_path}
        self._login_path = login_path

    async def complete(
        self, request: Request, assertion: ExternalAssertion
    ) -> Response:
        token = new_session_token()
        body = assertion.login_request(token, truncate_ip(_client_host(request)))
        op = identity_op("credential", "external_login", body)
        result = await call_expect(self._port, op, "authenticate")
        outcome = str(result.get("outcome"))
        target = self._targets.get(outcome)
        if target is None:
            code = _OUTCOME_ERRORS.get(outcome, "denied")
            return RedirectResponse(
                f"{self._login_path}?error={code}", status_code=303, headers=NO_STORE
            )
        response = RedirectResponse(target, status_code=303, headers=NO_STORE)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response


# ---------------------------------------------------------------------------
# Mapping-rule preview (admin dry-run)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DryRunResult:
    """What a sample assertion would receive from one IdP's rules."""

    matched_rules: tuple[str, ...]
    roles: tuple[str, ...]
    groups: tuple[str, ...]
    scopes: tuple[str, ...]
    privileged_rules: tuple[str, ...]


def _rule_matches(rule: Mapping[str, Any], claims: Mapping[str, Iterable[str]]) -> bool:
    kind = rule.get("match_kind")
    wanted = str(rule.get("value", ""))
    values = claims.get(str(rule.get("claim_path")), ())
    if kind == "equals":
        return any(value == wanted for value in values)
    if kind == "prefix":
        return any(value.startswith(wanted) for value in values)
    return False


def _targets(rules: Iterable[Mapping[str, Any]], kind: str) -> set[str]:
    """The ids of every ``<kind>:<id>`` target among ``rules``."""
    split = (str(rule.get("target", "")).partition(":") for rule in rules)
    return {ident for prefix, _, ident in split if prefix == kind}


def dry_run(
    idp: Mapping[str, Any],
    claims: Mapping[str, Iterable[str]],
    roles: Iterable[Mapping[str, Any]],
    groups: Iterable[Mapping[str, Any]],
) -> DryRunResult:
    """Evaluate ``idp``'s rules exactly as the engine does at sign-in: an
    ordered, deterministic UNION of every matching ``equals``/``prefix`` rule
    (the engine refuses any other match kind at upsert); ``role:<id>`` grants
    the role, ``group:<id>`` a membership whose group roles apply."""
    materialized = {path: list(values) for path, values in claims.items()}
    matched = [
        rule for rule in idp.get("rules", ()) if _rule_matches(rule, materialized)
    ]
    role_ids = _targets(matched, "role")
    group_ids = _targets(matched, "group")
    effective = role_ids | _collect(groups, "group_id", group_ids, "roles")
    scopes = _collect(roles, "role_id", effective, "scopes")
    privileged = [rule for rule in matched if rule.get("privileged")]
    return DryRunResult(
        matched_rules=_rule_ids(matched),
        roles=tuple(sorted(effective)),
        groups=tuple(sorted(group_ids)),
        scopes=tuple(sorted(scopes)),
        privileged_rules=_rule_ids(privileged),
    )


def _collect(
    records: Iterable[Mapping[str, Any]], key: str, wanted: set[str], field: str
) -> set[str]:
    """Every ``field`` value of the records whose ``key`` is in ``wanted``."""
    selected = [record for record in records if record.get(key) in wanted]
    return {str(value) for record in selected for value in record.get(field, ())}


def _rule_ids(rules: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(str(rule.get("rule_id")) for rule in rules)
