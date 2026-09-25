"""Browser WebAuthn ceremonies bind real signatures to EG-owned credentials."""

from __future__ import annotations

import hashlib
import json

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from graph_os.identity.web_webauthn import _b64

from .gate_harness import SETUP_CODE, serve
from .store_double import StoreDouble

ADMIN = {"username": "root", "password": "correct horse battery staple"}
RP_ID = "localhost"
ORIGIN = "https://localhost:8443"


def _served():
    served = serve(StoreDouble(), profile="single-node-prod")
    assert (
        served.post("/auth/setup", {"setup_code": SETUP_CODE, **ADMIN}).status_code
        == 200
    )
    return served


def _client_data(kind: str, challenge: str, *, origin: str = ORIGIN) -> bytes:
    return json.dumps(
        {"type": kind, "challenge": challenge, "origin": origin}, separators=(",", ":")
    ).encode()


def _credential(key: ec.EllipticCurvePrivateKey, challenge: str) -> dict:
    numbers = key.public_key().public_numbers()
    cose = {
        1: 2,
        3: -7,
        -1: 1,
        -2: numbers.x.to_bytes(32, "big"),
        -3: numbers.y.to_bytes(32, "big"),
    }
    credential_id = b"test-credential-1"
    auth_data = (
        hashlib.sha256(RP_ID.encode()).digest()
        + b"\x45"  # user presence, user verification, attested credential data
        + (0).to_bytes(4, "big")
        + bytes(16)
        + len(credential_id).to_bytes(2, "big")
        + credential_id
        + cbor2.dumps(cose)
    )
    return {
        "id": _b64(credential_id),
        "rawId": _b64(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64(_client_data("webauthn.create", challenge)),
            "attestationObject": _b64(
                cbor2.dumps({"fmt": "none", "authData": auth_data, "attStmt": {}})
            ),
            "transports": ["internal"],
        },
        "clientExtensionResults": {},
    }


def _assertion(
    key: ec.EllipticCurvePrivateKey,
    challenge: str,
    *,
    origin: str = ORIGIN,
    count: int = 1,
) -> dict:
    client_data = _client_data("webauthn.get", challenge, origin=origin)
    auth_data = (
        hashlib.sha256(RP_ID.encode()).digest()
        + b"\x05"  # user presence and user verification
        + count.to_bytes(4, "big")
    )
    signature = key.sign(
        auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
    )
    credential_id = _b64(b"test-credential-1")
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64(client_data),
            "authenticatorData": _b64(auth_data),
            "signature": _b64(signature),
            "userHandle": None,
        },
        "clientExtensionResults": {},
    }


def test_registration_and_assertion_complete_pending_session() -> None:
    served = _served()
    key = ec.generate_private_key(ec.SECP256R1())
    begin = served.post("/auth/mfa/webauthn/register")
    assert begin.status_code == 200
    registered = served.post(
        "/auth/mfa/webauthn/register-complete",
        {
            "name": "test passkey",
            "credential": _credential(key, begin.json()["challenge"]),
        },
    )
    assert registered.status_code == 201, registered.text
    served.session = None
    assert served.post("/auth/login", ADMIN).json()["outcome"] == "mfa_required"
    assert served.get("/api/echo").status_code == 401
    options = served.post("/auth/mfa/webauthn/authenticate")
    assert options.status_code == 200
    result = served.post(
        "/auth/mfa/webauthn/authenticate-complete",
        {"credential": _assertion(key, options.json()["challenge"])},
    )
    assert result.status_code == 200, result.text
    assert result.json() == {"outcome": "ok"}
    assert served.get("/api/echo").json()["sub"] is not None
    served.session = None
    served.post("/auth/login", ADMIN)
    replay_options = served.post("/auth/mfa/webauthn/authenticate")
    replay = served.post(
        "/auth/mfa/webauthn/authenticate-complete",
        {"credential": _assertion(key, replay_options.json()["challenge"], count=1)},
    )
    assert replay.status_code == 401
    assert served.get("/api/echo").status_code == 401


def test_challenge_is_single_use_and_wrong_origin_cannot_advance_session() -> None:
    served = _served()
    key = ec.generate_private_key(ec.SECP256R1())
    begin = served.post("/auth/mfa/webauthn/register")
    attestation = _credential(key, begin.json()["challenge"])
    assert (
        served.post(
            "/auth/mfa/webauthn/register-complete",
            {"name": "test passkey", "credential": attestation},
        ).status_code
        == 201
    )
    assert (
        served.post(
            "/auth/mfa/webauthn/register-complete",
            {"name": "test passkey", "credential": attestation},
        ).status_code
        == 400
    )
    served.session = None
    served.post("/auth/login", ADMIN)
    options = served.post("/auth/mfa/webauthn/authenticate")
    assertion = _assertion(
        key, options.json()["challenge"], origin="https://evil.example"
    )
    assert (
        served.post(
            "/auth/mfa/webauthn/authenticate-complete", {"credential": assertion}
        ).status_code
        == 401
    )
    assert (
        served.post(
            "/auth/mfa/webauthn/authenticate-complete",
            {"credential": _assertion(key, options.json()["challenge"])},
        ).status_code
        == 400
    )
    assert served.get("/api/echo").status_code == 401


def test_invalid_signature_cannot_advance_session_or_counter() -> None:
    served = _served()
    key = ec.generate_private_key(ec.SECP256R1())
    begin = served.post("/auth/mfa/webauthn/register")
    assert (
        served.post(
            "/auth/mfa/webauthn/register-complete",
            {
                "name": "test passkey",
                "credential": _credential(key, begin.json()["challenge"]),
            },
        ).status_code
        == 201
    )
    served.session = None
    served.post("/auth/login", ADMIN)
    options = served.post("/auth/mfa/webauthn/authenticate")
    attacker = ec.generate_private_key(ec.SECP256R1())
    forged = _assertion(attacker, options.json()["challenge"])
    assert (
        served.post(
            "/auth/mfa/webauthn/authenticate-complete", {"credential": forged}
        ).status_code
        == 401
    )
    assert served.get("/api/echo").status_code == 401
    admin = next(
        user for user in served.store.users.values() if user.username == "root"
    )
    assert admin.webauthn[forged["id"]]["sign_count"] == 0


def test_unknown_step_and_missing_session_refuse() -> None:
    served = _served()
    assert served.post("/auth/mfa/webauthn/unknown").status_code == 404
    served.session = None
    assert served.post("/auth/mfa/webauthn/register").status_code == 401
