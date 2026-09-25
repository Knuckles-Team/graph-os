"""The identity broker: every sign-in, session and credential flow (IDM-06..11).

The broker is the one place a GraphOS surface (browser routes, MCP bearer
verification, the operator CLI) turns a credential into a principal. It holds
no identity state: the engine store answers every question, and the broker
only generates the caller-side secrets, mints the access token for the
resolved principal, and keeps a short cache of the auth-mode singleton.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .engine import IdentityCall, IdentityEngine, IdentityRefused, Resolution, SignIn
from .issuer import LocalIssuer, Retirement, TokenGrant
from .material import (
    ApiKeySecret,
    new_api_key,
    new_recovery_codes,
    new_token,
    new_totp_secret,
    parse_api_key,
    totp_provisioning_uri,
)

__all__ = ["ApiKeyIssue", "IdentityBroker", "OpenedSession", "TotpEnrollment"]

#: How long the cached auth-mode singleton is trusted before a re-read.
_CONFIG_TTL_SECONDS = 5.0
#: API keys live at most one year (design §3.2); default ninety days.
API_KEY_DEFAULT_TTL_MS = 90 * 24 * 60 * 60 * 1000
#: An administrator-issued reset token lives thirty minutes (§5.1).
RESET_TOKEN_TTL_MS = 30 * 60 * 1000


@dataclass(frozen=True)
class OpenedSession:
    """A sign-in's outcome and, when the engine opened one, its session id."""

    sign_in: SignIn
    session_token: str | None


@dataclass(frozen=True)
class TotpEnrollment:
    """The new factor's secret and URI, shown to the user exactly once."""

    secret: str
    provisioning_uri: str


@dataclass(frozen=True)
class ApiKeyIssue:
    """A new API key: shown once, never retrievable again."""

    key_id: str
    presented: str


#: Refusals that mean "this credential names nothing usable".
_UNKNOWN_CREDENTIAL = frozenset({"IDENTITY_NOT_FOUND", "IDENTITY_INVALID"})


def _secret_request(**fields: Any) -> dict[str, Any]:
    return {name: value for name, value in fields.items() if value is not None}


async def _resolution_or_none(engine: IdentityEngine, call: IdentityCall) -> Resolution | None:
    """A credential lookup: an unknown or malformed credential is ``None``."""
    try:
        reply = await engine.broker(call)
    except IdentityRefused as refused:
        if refused.code in _UNKNOWN_CREDENTIAL:
            return None
        raise
    return Resolution.parse(reply.expect("resolution"))


class IdentityBroker:
    """Credential → principal → access token, over the engine identity store."""

    def __init__(
        self,
        engine: IdentityEngine,
        issuer: LocalIssuer,
        *,
        issuer_label: str = "GraphOS",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._issuer = issuer
        self._label = issuer_label
        self._clock = clock
        self._config: tuple[float, Mapping[str, Any]] | None = None

    @property
    def issuer(self) -> LocalIssuer:
        return self._issuer

    @property
    def engine(self) -> IdentityEngine:
        return self._engine

    # -- the auth-mode singleton -------------------------------------------

    async def config(self, *, fresh: bool = False) -> Mapping[str, Any]:
        """The engine's identity config (cached briefly; ``fresh`` re-reads)."""
        now = self._clock()
        if not fresh and self._config and now - self._config[0] < _CONFIG_TTL_SECONDS:
            return self._config[1]
        reply = await self._engine.broker(IdentityCall("config", "get"))
        config = reply.expect("config")
        self._config = (now, config)
        return config

    async def mode(self) -> str:
        return str((await self.config())["mode"])

    def forget_config(self) -> None:
        self._config = None

    async def initialize(
        self, mode: str, admin_username: str | None = None, admin_password: str | None = None
    ) -> str:
        """First-run seeding; ``local`` requires the first administrator."""
        request = _secret_request(
            mode=mode, admin_username=admin_username, admin_password=admin_password
        )
        reply = await self._engine.broker(IdentityCall("config", "initialize", request))
        self.forget_config()
        return str(reply.expect("principal")["principal_id"])

    async def transition(
        self, caller: Any, to: str, *, ack: str | None = None, local_fallback: str | None = None
    ) -> Mapping[str, Any]:
        """Move the mode one edge, rotating the issuer key first.

        Leaving or entering ``none`` revokes every earlier key at once;
        other edges keep the outgoing key for one token lifetime.
        """
        config = await self.config(fresh=True)
        retirement = (
            Retirement.REVOKE if "none" in (config["mode"], to) else Retirement.OVERLAP
        )
        kid = self._issuer.rotate(retirement)
        request = _secret_request(
            expected_epoch=config["epoch"],
            to=to,
            ack=ack,
            local_fallback=local_fallback,
            issuer_kid=kid,
        )
        reply = await self._engine.as_caller(
            caller, IdentityCall("config", "transition", request)
        )
        self.forget_config()
        return reply.expect("config")

    # -- sign-in and sessions ----------------------------------------------

    async def sign_in(
        self,
        username: str,
        password: str,
        *,
        ip_prefix: str | None = None,
        new_password: str | None = None,
    ) -> OpenedSession:
        token = new_token()
        request = _secret_request(
            username=username,
            password=password,
            session_token=token,
            ip_prefix=ip_prefix,
            new_password=new_password,
        )
        reply = await self._engine.broker(
            IdentityCall("credential", "authenticate", request)
        )
        sign_in = SignIn.parse(reply.expect("authenticate"))
        opened = sign_in.outcome in {"ok", "mfa_required", "mfa_enrollment_required"}
        return OpenedSession(sign_in, token if opened else None)

    async def bootstrap_session(self) -> str:
        """``none`` mode: a session for the bootstrap principal."""
        token = new_token()
        await self._engine.broker(
            IdentityCall("credential", "bootstrap_session", {"session_token": token})
        )
        return token

    async def resolve_session(self, session_token: str) -> Resolution | None:
        """The live session's principal, or ``None`` (unknown/expired/revoked)."""
        return await _resolution_or_none(
            self._engine,
            IdentityCall("session", "resolve", {"session_token": session_token}),
        )

    async def sign_out(self, session_token: str) -> None:
        await self._engine.broker(
            IdentityCall("session", "revoke", {"session_token": session_token})
        )

    def access_token(
        self, resolution: Resolution, methods: Sequence[str], *, scopes: frozenset[str] | None = None
    ) -> str:
        grant = TokenGrant(tuple(methods), int(self._clock()), scopes)
        return self._issuer.mint(resolution, grant)

    # -- second factors ----------------------------------------------------

    async def second_factor(self, session_token: str, code: str, *, recovery: bool) -> SignIn:
        """Complete a pending session with a TOTP code or a recovery code."""
        family_op = ("mfa", "consume_recovery_code") if recovery else ("mfa", "verify_totp")
        reply = await self._engine.broker(
            IdentityCall(*family_op, {"session_token": session_token, "code": code})
        )
        return SignIn.parse(reply.expect("authenticate"))

    async def enroll_totp(self, session_token: str, account: str) -> TotpEnrollment:
        secret = new_totp_secret()
        await self._engine.broker(
            IdentityCall(
                "mfa", "enroll_totp", {"session_token": session_token, "secret_base32": secret}
            )
        )
        return TotpEnrollment(secret, totp_provisioning_uri(secret, account, self._label))

    async def confirm_totp(self, session_token: str, code: str) -> None:
        await self._engine.broker(
            IdentityCall("mfa", "confirm_totp", {"session_token": session_token, "code": code})
        )

    async def regenerate_recovery_codes(self, session_token: str) -> list[str]:
        codes = new_recovery_codes()
        await self._engine.broker(
            IdentityCall(
                "mfa", "set_recovery_codes", {"session_token": session_token, "codes": codes}
            )
        )
        return codes

    # -- passwords and one-time tokens -------------------------------------

    async def issue_admin_reset(self, admin_session_token: str, principal_id: str) -> str:
        """A single-use reset token for ``principal_id``, shown to the admin once."""
        token = new_token()
        request = {
            "session_token": admin_session_token,
            "purpose": "admin_reset",
            "principal_id": principal_id,
            "token": token,
            "ttl_ms": RESET_TOKEN_TTL_MS,
        }
        await self._engine.broker(IdentityCall("token", "issue_one_time", request))
        return token

    async def redeem_reset(self, purpose: str, token: str, new_password: str) -> str:
        request = {"purpose": purpose, "token": token, "new_password": new_password}
        reply = await self._engine.broker(IdentityCall("token", "redeem_one_time", request))
        return str(reply.expect("principal")["principal_id"])

    async def change_password(self, caller: Any, current: str, new: str) -> None:
        await self._engine.as_caller(
            caller,
            IdentityCall("credential", "change_password", {"current": current, "new": new}),
        )

    # -- API keys ------------------------------------------------------------

    async def issue_api_key(
        self,
        admin_session_token: str,
        principal_id: str,
        scopes: Sequence[str],
        ttl_ms: int = API_KEY_DEFAULT_TTL_MS,
    ) -> ApiKeyIssue:
        key: ApiKeySecret = new_api_key()
        request = {
            "session_token": admin_session_token,
            "principal_id": principal_id,
            "key_id": key.key_id,
            "secret": key.secret,
            "scopes": sorted(set(scopes)),
            "ttl_ms": ttl_ms,
        }
        await self._engine.broker(IdentityCall("token", "issue_api_key", request))
        return ApiKeyIssue(key.key_id, key.presented)

    async def verify_api_key(self, presented: str) -> Resolution | None:
        """The key's CURRENT authority (its scopes ∩ its owner's), or ``None``."""
        key = parse_api_key(presented)
        if key is None:
            return None
        return await _resolution_or_none(
            self._engine,
            IdentityCall("token", "verify_api_key", {"key_id": key.key_id, "secret": key.secret}),
        )

    async def revoke_api_key(self, caller: Any, key_id: str) -> None:
        await self._engine.as_caller(
            caller, IdentityCall("token", "revoke_api_key", {"id": key_id})
        )
