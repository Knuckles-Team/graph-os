"""An in-memory stand-in for the engine identity store (``Method::Identity``).

It speaks the same wire shape as the engine (``IdentityCall.wire()`` in,
``{"kind": ..., "value": ...}`` out, typed ``IDENTITY_*`` refusals) and keeps
just enough of the engine's semantics for the broker's flows: uniform ``bad``
sign-ins, server-side sessions, single-use one-time tokens, API keys narrowed
to their owner's CURRENT scopes, TOTP-pending sessions and the mode singleton
with its epoch CAS. Secrets are compared in memory; the real store hashes them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from graph_os.identity.engine import IdentityCall, IdentityRefused, IdentityReply

from .fakes import FakeSecrets

__all__ = ["SecretsDouble", "StoreDouble", "StoredUser", "TOTP_GOOD_CODE"]

#: The secrets backend double is shared with the external-authority tests.
SecretsDouble = FakeSecrets

#: The one TOTP code the double accepts (the engine checks RFC 6238).
TOTP_GOOD_CODE = "123456"
BOOTSTRAP = "usr:bootstrap"
ADMIN_SCOPES = frozenset({"kg:admin", "webui:admin", "identity:admin", "identity:self"})
USER_SCOPES = frozenset({"kg:read", "identity:self"})


@dataclass
class StoredUser:
    principal_id: str
    username: str
    kind: str = "human"
    status: str = "active"
    password: str | None = None
    scopes: frozenset[str] = USER_SCOPES
    is_bootstrap: bool = False
    totp: str | None = None
    totp_confirmed: bool = False
    recovery: list[str] = field(default_factory=list)

    def resolution(self, *, pending: bool = False) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "username": self.username,
            "kind": self.kind,
            "status": self.status,
            "is_bootstrap": self.is_bootstrap,
            "roles": [],
            "groups": [],
            "scopes": sorted(self.scopes),
            "mfa_required": False,
            "mfa_enrolled": self.totp_confirmed,
            "session_mfa_pending": pending,
        }


@dataclass
class _Session:
    principal_id: str
    mfa_pending: bool = False
    revoked: bool = False


def _refuse(code: str) -> IdentityRefused:
    return IdentityRefused(code)


class StoreDouble:
    """The engine identity store port, in memory."""

    def __init__(self) -> None:
        self.config: dict[str, Any] | None = None
        self.users: dict[str, StoredUser] = {}
        self.sessions: dict[str, _Session] = {}
        self.one_time: dict[str, tuple[str, str]] = {}
        self.api_keys: dict[str, tuple[str, str, frozenset[str], bool]] = {}
        self.caller_calls: list[tuple[str, str, str]] = []
        self.available = True
        self._caller = ""
        self._serial = 0
        self._ops: Mapping[
            tuple[str, str], Callable[[Mapping[str, Any]], IdentityReply]
        ] = {
            ("config", "get"): self._config_get,
            ("config", "initialize"): self._initialize,
            ("config", "transition"): self._transition,
            ("credential", "authenticate"): self._authenticate,
            ("credential", "bootstrap_session"): self._bootstrap_session,
            ("credential", "change_password"): self._change_password,
            ("credential", "external_login"): self._external_login,
            ("session", "resolve"): self._resolve_session,
            ("session", "revoke"): self._revoke_session,
            ("user", "create"): self._create_user,
            ("user", "update"): self._update_user,
            ("user", "list"): self._list_users,
            ("credential", "set_password"): self._set_password,
            ("token", "issue_one_time"): self._issue_one_time,
            ("token", "redeem_one_time"): self._redeem_one_time,
            ("token", "issue_api_key"): self._issue_api_key,
            ("token", "verify_api_key"): self._verify_api_key,
            ("token", "revoke_api_key"): self._revoke_api_key,
            ("mfa", "enroll_totp"): self._enroll_totp,
            ("mfa", "confirm_totp"): self._confirm_totp,
            ("mfa", "verify_totp"): self._verify_totp,
            ("mfa", "set_recovery_codes"): self._set_recovery,
            ("mfa", "consume_recovery_code"): self._consume_recovery,
        }

    # -- the port --------------------------------------------------------

    async def broker(self, call: IdentityCall) -> IdentityReply:
        return self._dispatch(call)

    async def as_caller(self, session: Any, call: IdentityCall) -> IdentityReply:
        self.caller_calls.append((str(session.actor.actor_id), call.family, call.op))
        self._caller = str(session.actor.actor_id)
        return self._dispatch(call)

    def _dispatch(self, call: IdentityCall) -> IdentityReply:
        from graph_os.identity.engine import IdentityUnavailable

        if not self.available:
            raise IdentityUnavailable("store double is down")
        wire = call.wire()
        handler = self._ops.get((wire["family"], wire["op"]))
        if handler is None:
            raise _refuse("IDENTITY_INVALID")
        return handler(wire.get("request") or {})

    # -- helpers ---------------------------------------------------------

    def add_user(
        self, username: str, password: str | None, **fields: Any
    ) -> StoredUser:
        self._serial += 1
        principal = fields.pop("principal_id", f"usr:{self._serial:04d}")
        user = StoredUser(principal, username, password=password, **fields)
        self.users[principal] = user
        return user

    def _by_name(self, username: str) -> StoredUser | None:
        return next((u for u in self.users.values() if u.username == username), None)

    def _live(self, token: str) -> _Session:
        session = self.sessions.get(token)
        if session is None or session.revoked:
            raise _refuse("IDENTITY_NOT_FOUND")
        return session

    def _require_config(self) -> dict[str, Any]:
        if self.config is None:
            raise _refuse("IDENTITY_NOT_INITIALIZED")
        return self.config

    @staticmethod
    def _authenticated(principal: str) -> IdentityReply:
        return IdentityReply(
            "authenticate", {"outcome": "ok", "principal_id": principal}
        )

    # -- config ----------------------------------------------------------

    def _config_get(self, _: Mapping[str, Any]) -> IdentityReply:
        return IdentityReply("config", dict(self._require_config()))

    def _initialize(self, request: Mapping[str, Any]) -> IdentityReply:
        if self.config is not None:
            raise _refuse("IDENTITY_ALREADY_INITIALIZED")
        mode = str(request["mode"])
        self.users[BOOTSTRAP] = StoredUser(
            BOOTSTRAP, "bootstrap", scopes=ADMIN_SCOPES, is_bootstrap=True
        )
        principal = BOOTSTRAP
        if mode == "local":
            if not request.get("admin_username") or not request.get("admin_password"):
                raise _refuse("IDENTITY_PRECONDITION_FAILED")
            principal = self.add_user(
                str(request["admin_username"]),
                str(request["admin_password"]),
                scopes=ADMIN_SCOPES,
            ).principal_id
        self.config = {
            "mode": mode,
            "local_fallback": "off",
            "registration_policy": "admin_only",
            "epoch": 1,
        }
        return IdentityReply("principal", {"principal_id": principal})

    def _transition(self, request: Mapping[str, Any]) -> IdentityReply:
        config = self._require_config()
        if request["expected_epoch"] != config["epoch"]:
            raise _refuse("IDENTITY_EPOCH_CONFLICT")
        config.update(
            mode=request["to"],
            epoch=config["epoch"] + 1,
            issuer_kid_current=request["issuer_kid"],
        )
        for session in self.sessions.values():
            session.revoked = True
        return IdentityReply("config", dict(config))

    # -- credentials and sessions ----------------------------------------

    def _authenticate(self, request: Mapping[str, Any]) -> IdentityReply:
        user = self._by_name(str(request["username"]))
        if (
            user is None
            or user.status != "active"
            or user.password != request["password"]
        ):
            return IdentityReply("authenticate", {"outcome": "bad"})
        pending = user.totp_confirmed
        self.sessions[str(request["session_token"])] = _Session(
            user.principal_id, pending
        )
        outcome = "mfa_required" if pending else "ok"
        return IdentityReply(
            "authenticate", {"outcome": outcome, "principal_id": user.principal_id}
        )

    def _bootstrap_session(self, request: Mapping[str, Any]) -> IdentityReply:
        if self._require_config()["mode"] != "none":
            raise _refuse("IDENTITY_NOT_AUTHORIZED")
        self.sessions[str(request["session_token"])] = _Session(BOOTSTRAP)
        return self._authenticated(BOOTSTRAP)

    def _external_login(self, request: Mapping[str, Any]) -> IdentityReply:
        principal = f"usr:ext-{request['subject']}"
        if principal not in self.users:
            self.users[principal] = StoredUser(principal, str(request["subject"]))
        self.sessions[str(request["session_token"])] = _Session(principal)
        return self._authenticated(principal)

    def _change_password(self, request: Mapping[str, Any]) -> IdentityReply:
        user = self.users[self._caller]
        if user.password != request["current"]:
            raise _refuse("IDENTITY_INVALID")
        user.password = str(request["new"])
        return IdentityReply("done", {"changed": True})

    def _resolve_session(self, request: Mapping[str, Any]) -> IdentityReply:
        session = self._live(str(request["session_token"]))
        user = self.users[session.principal_id]
        return IdentityReply("resolution", user.resolution(pending=session.mfa_pending))

    def _revoke_session(self, request: Mapping[str, Any]) -> IdentityReply:
        self._live(str(request["session_token"])).revoked = True
        return IdentityReply("done", {"changed": True})

    def _create_user(self, request: Mapping[str, Any]) -> IdentityReply:
        if self._by_name(str(request["username"])) is not None:
            raise _refuse("IDENTITY_COLLISION")
        user = self.add_user(str(request["username"]), request.get("password"))
        return IdentityReply("principal", {"principal_id": user.principal_id})

    def _update_user(self, request: Mapping[str, Any]) -> IdentityReply:
        user = self.users[str(request["principal_id"])]
        user.username = str(request.get("username") or user.username)
        return IdentityReply("done", {"changed": True})

    def _set_password(self, request: Mapping[str, Any]) -> IdentityReply:
        self.users[str(request["principal_id"])].password = str(request["password"])
        return IdentityReply("done", {"changed": True})

    def _list_users(self, _: Mapping[str, Any]) -> IdentityReply:
        views = [
            {"principal_id": u.principal_id, "username": u.username}
            for u in sorted(self.users.values(), key=lambda u: u.principal_id)
        ]
        return IdentityReply("users", views)

    # -- one-time tokens and API keys ------------------------------------

    def _admin_of(self, session_token: str) -> StoredUser:
        user = self.users[self._live(session_token).principal_id]
        if "identity:admin" not in user.scopes:
            raise _refuse("IDENTITY_NOT_AUTHORIZED")
        return user

    def _issue_one_time(self, request: Mapping[str, Any]) -> IdentityReply:
        self._admin_of(str(request["session_token"]))
        self.one_time[str(request["token"])] = (
            str(request["purpose"]),
            str(request["principal_id"]),
        )
        return IdentityReply("done", {"changed": True})

    def _redeem_one_time(self, request: Mapping[str, Any]) -> IdentityReply:
        entry = self.one_time.pop(str(request["token"]), None)
        if entry is None or entry[0] != request["purpose"]:
            raise _refuse("IDENTITY_INVALID")
        self.users[entry[1]].password = str(request["new_password"])
        return IdentityReply("principal", {"principal_id": entry[1]})

    def _issue_api_key(self, request: Mapping[str, Any]) -> IdentityReply:
        self._admin_of(str(request["session_token"]))
        owner = self.users[str(request["principal_id"])]
        scopes = frozenset(request["scopes"])
        if not scopes <= owner.scopes:
            raise _refuse("IDENTITY_CLASS_VIOLATION")
        self.api_keys[str(request["key_id"])] = (
            str(request["secret"]),
            owner.principal_id,
            scopes,
            False,
        )
        return IdentityReply("done", {"changed": True})

    def _verify_api_key(self, request: Mapping[str, Any]) -> IdentityReply:
        entry = self.api_keys.get(str(request["key_id"]))
        if entry is None or entry[3] or entry[0] != request["secret"]:
            raise _refuse("IDENTITY_NOT_FOUND")
        owner = self.users[entry[1]]
        resolution = owner.resolution()
        resolution["scopes"] = sorted(entry[2] & owner.scopes)
        return IdentityReply("resolution", resolution)

    def _revoke_api_key(self, request: Mapping[str, Any]) -> IdentityReply:
        secret, owner, scopes, _ = self.api_keys[str(request["id"])]
        self.api_keys[str(request["id"])] = (secret, owner, scopes, True)
        return IdentityReply("done", {"changed": True})

    # -- second factors --------------------------------------------------

    def _session_user(self, request: Mapping[str, Any]) -> tuple[_Session, StoredUser]:
        session = self._live(str(request["session_token"]))
        return session, self.users[session.principal_id]

    def _enroll_totp(self, request: Mapping[str, Any]) -> IdentityReply:
        _, user = self._session_user(request)
        user.totp = str(request["secret_base32"])
        return IdentityReply("done", {"changed": True})

    def _confirm_totp(self, request: Mapping[str, Any]) -> IdentityReply:
        _, user = self._session_user(request)
        if user.totp is None or request.get("code") != TOTP_GOOD_CODE:
            raise _refuse("IDENTITY_INVALID")
        user.totp_confirmed = True
        return IdentityReply("done", {"changed": True})

    def _second_factor(
        self, request: Mapping[str, Any], accepted: bool
    ) -> IdentityReply:
        session, user = self._session_user(request)
        if not accepted:
            return IdentityReply("authenticate", {"outcome": "bad"})
        session.mfa_pending = False
        return self._authenticated(user.principal_id)

    def _verify_totp(self, request: Mapping[str, Any]) -> IdentityReply:
        return self._second_factor(request, request.get("code") == TOTP_GOOD_CODE)

    def _set_recovery(self, request: Mapping[str, Any]) -> IdentityReply:
        _, user = self._session_user(request)
        user.recovery = [str(code) for code in request["codes"]]
        return IdentityReply("done", {"changed": True})

    def _consume_recovery(self, request: Mapping[str, Any]) -> IdentityReply:
        _, user = self._session_user(request)
        code = str(request.get("code"))
        accepted = code in user.recovery
        if accepted:
            user.recovery.remove(code)
        return self._second_factor(request, accepted)
