"""Browser WebAuthn ceremonies; only verified public material reaches EG.

Challenges are short-lived, single-use and bound to a live browser session. A
request routed to another worker has no matching challenge and is refused; a
caller can start a new ceremony there. No client-supplied origin or RP ID is
trusted. The issuer URL is the relying party's configured origin.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .admission import AdmissionService
from .web_common import RouteError, caller_of, json_body, string_field

_CHALLENGE_TTL = 120.0
_MAX_CHALLENGES = 1024


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _library() -> Any:
    try:
        import webauthn
    except ImportError:
        raise RouteError(503, "webauthn_dependency_unavailable") from None
    return webauthn


def _origin(issuer: str) -> tuple[str, str]:
    parsed = urlsplit(issuer)
    hostname = parsed.hostname
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and (
                parsed.scheme != "http"
                or hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise RouteError(503, "webauthn_origin_invalid")
    return f"{parsed.scheme}://{parsed.netloc}", hostname


@dataclass(frozen=True)
class _Challenge:
    value: bytes
    expires: float
    principal_id: str


class WebauthnCeremonies:
    """One ceremony per session and purpose, with bounded memory and replay refusal."""

    def __init__(self, admission: AdmissionService) -> None:
        self._admission = admission
        self._broker = admission.broker
        self._challenges: OrderedDict[tuple[str, str], _Challenge] = OrderedDict()

    def _key(self, session: str, purpose: str) -> tuple[str, str]:
        return hashlib.sha256(session.encode()).hexdigest(), purpose

    def _issue(self, session: str, purpose: str, principal_id: str) -> bytes:
        now = time.monotonic()
        for key, state in tuple(self._challenges.items()):
            if state.expires <= now:
                del self._challenges[key]
        while len(self._challenges) >= _MAX_CHALLENGES:
            self._challenges.popitem(last=False)
        value = secrets.token_bytes(32)
        self._challenges[self._key(session, purpose)] = _Challenge(
            value, now + _CHALLENGE_TTL, principal_id
        )
        return value

    def _consume(self, session: str, purpose: str, principal_id: str) -> bytes:
        state = self._challenges.pop(self._key(session, purpose), None)
        if state is None or state.expires <= time.monotonic():
            raise RouteError(400, "webauthn_challenge_expired")
        if state.principal_id != principal_id:
            raise RouteError(403, "webauthn_principal_changed")
        return state.value

    async def _credentials(self, session: str) -> list[dict[str, Any]]:
        values = await self._broker.webauthn_credentials(session)
        if not isinstance(values, list):
            raise RouteError(503, "webauthn_credentials_invalid")
        return values

    async def register(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request)
        library = _library()
        _, rp_id = _origin(self._broker.issuer.settings.issuer)
        credentials = await self._credentials(caller.session_token)
        from webauthn.helpers import options_to_json
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor

        challenge = self._issue(
            caller.session_token, "register", caller.resolution.principal_id
        )
        options = library.generate_registration_options(
            rp_id=rp_id,
            rp_name="GraphOS",
            user_id=hashlib.sha256(caller.resolution.principal_id.encode()).digest(),
            user_name=caller.resolution.username,
            challenge=challenge,
            exclude_credentials=[
                PublicKeyCredentialDescriptor(
                    id=library.base64url_to_bytes(row["credential_id"])
                )
                for row in credentials
            ],
        )
        return JSONResponse(
            json.loads(options_to_json(options)), headers={"cache-control": "no-store"}
        )

    async def register_complete(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request)
        body = await json_body(request)
        credential = body.get("credential")
        if not isinstance(credential, dict):
            raise RouteError(400, "credential_invalid")
        name = string_field(body, "name")
        challenge = self._consume(
            caller.session_token, "register", caller.resolution.principal_id
        )
        library = _library()
        from webauthn.helpers.exceptions import WebAuthnException

        origin, rp_id = _origin(self._broker.issuer.settings.issuer)
        try:
            result = library.verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError):
            raise RouteError(400, "webauthn_attestation_invalid") from None
        response = credential.get("response")
        transports = (
            response.get("transports", []) if isinstance(response, dict) else []
        )
        if not isinstance(transports, list) or not all(
            isinstance(x, str) for x in transports
        ):
            raise RouteError(400, "transports_invalid")
        await self._broker.register_webauthn(
            caller.session_token,
            {
                "credential_id": _b64(result.credential_id),
                "public_key_cose": _b64(result.credential_public_key),
                "sign_count": result.sign_count,
                "aaguid": None,
                "transports": transports,
                "name": name,
            },
        )
        return JSONResponse({"registered": True}, status_code=201)

    async def authenticate(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request, pending_ok=True)
        if not caller.resolution.session_mfa_pending:
            raise RouteError(409, "webauthn_not_pending")
        library = _library()
        _, rp_id = _origin(self._broker.issuer.settings.issuer)
        credentials = await self._credentials(caller.session_token)
        if not credentials:
            raise RouteError(409, "webauthn_not_enrolled")
        from webauthn.helpers import options_to_json
        from webauthn.helpers.structs import (
            PublicKeyCredentialDescriptor,
            UserVerificationRequirement,
        )

        challenge = self._issue(
            caller.session_token, "authenticate", caller.resolution.principal_id
        )
        options = library.generate_authentication_options(
            rp_id=rp_id,
            challenge=challenge,
            allow_credentials=[
                PublicKeyCredentialDescriptor(
                    id=library.base64url_to_bytes(row["credential_id"])
                )
                for row in credentials
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return JSONResponse(
            json.loads(options_to_json(options)), headers={"cache-control": "no-store"}
        )

    async def authenticate_complete(self, request: Request) -> Response:
        caller = await caller_of(self._admission, request, pending_ok=True)
        if not caller.resolution.session_mfa_pending:
            raise RouteError(409, "webauthn_not_pending")
        body = await json_body(request)
        credential = body.get("credential")
        if not isinstance(credential, dict):
            raise RouteError(400, "credential_invalid")
        credential_id = credential.get("id")
        if not isinstance(credential_id, str):
            raise RouteError(400, "credential_invalid")
        challenge = self._consume(
            caller.session_token, "authenticate", caller.resolution.principal_id
        )
        library = _library()
        from webauthn.helpers.exceptions import WebAuthnException

        origin, rp_id = _origin(self._broker.issuer.settings.issuer)
        credentials = await self._credentials(caller.session_token)
        stored = next(
            (row for row in credentials if row["credential_id"] == credential_id), None
        )
        if stored is None:
            raise RouteError(401, "webauthn_assertion_invalid")
        try:
            result = library.verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=library.base64url_to_bytes(
                    stored["public_key_cose"]
                ),
                credential_current_sign_count=stored["sign_count"],
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError):
            raise RouteError(401, "webauthn_assertion_invalid") from None
        signed_in = await self._broker.verify_webauthn(
            caller.session_token, credential_id, result.new_sign_count
        )
        status = 200 if signed_in.outcome == "ok" else 401
        return JSONResponse({"outcome": signed_in.outcome}, status_code=status)
